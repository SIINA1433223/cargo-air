"""新建记录（索引页「新建商机」）的读写契约。

桩在 ``_http_json`` 这一层：只验**发出去的是什么**与**什么都没发**两种情形，
不打飞书、不碰任何真实业务数据。
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Coroutine
from typing import Any, cast

import pytest
from redis.asyncio.client import Redis

from app.core.exceptions import CustomException
from app.plugin.module_feishu import service as service_module
from app.plugin.module_feishu.service import FeishuBitableService

ROLE = "wd_customer_service"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def use_config(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    """把真实工作台配置换掉几个字段后再交回去。

    这里要测的是**机制**（排除名单怎么滤、默认值怎么换 id），而不是某一天的业务配置——
    「哪些人不列出」业务随时会改，用例不该跟着改。`get_workbench` 仍走真的那一个，
    未知角色照常报错。

    基函数**在定义时就抓下来**而不是调用时再查模块属性：fixture 与用例都可能各调一次，
    每次都会再包一层，调用时才查的话包在最外面的那个会查到自己，直接无限递归。
    """

    base = service_module.get_workbench

    def fake(role: str) -> Any:
        return dataclasses.replace(base(role), **changes)

    monkeypatch.setattr(service_module, "get_workbench", fake)


class FakeCreate(FeishuBitableService):
    """内存版：字段类型自己给，HTTP 这一层由用例替换掉。"""

    def __init__(self, types: dict[str, int]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.types = types
        # (method, url, json) 三元组，断言发出去的内容
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.response: dict[str, Any] = {"data": {"record": {"record_id": "rec_new"}}}

    async def _field_types(self) -> dict[str, int]:
        return dict(self.types)

    async def _endpoint(self, path: str) -> str:
        return f"https://example.invalid{path}"

    async def _token(self) -> str:
        return "fake-token"

    async def call(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        self.sent.append((method, url, kwargs.get("json") or {}))
        return self.response


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeCreate:
    """字段类型：文本/数字/单选/日期/勾选可写，公式与自动编号只读。"""
    instance = FakeCreate(
        {
            "客户 REF NO.": 1,
            "件数": 2,
            "业务类型": 3,
            "货好时间": 5,
            "是否拿到货": 7,
            "商机编号": 1005,  # 自动编号：只读
            "货物数据": 20,  # 公式：只读
        }
    )

    async def fake_http(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        return await instance.call(method, url, token, **kwargs)

    monkeypatch.setattr(service_module, "_http_json", fake_http)
    return instance


def test_新建记录_走_post_records_并回传_record_id(fake: FakeCreate) -> None:
    result = run(fake.create_workbench_record({"客户 REF NO.": "ABC-1", "件数": 12}, ROLE))

    assert result == {"record_id": "rec_new", "written": 2}
    method, url, body = fake.sent[0]
    assert method == "POST"
    assert url.endswith("/records")
    assert body == {"fields": {"客户 REF NO.": "ABC-1", "件数": 12}}


def test_只读字段被丢掉_不会送进飞书(fake: FakeCreate) -> None:
    """公式/自动编号由飞书自己算，送过去只会被整单拒掉。"""
    run(fake.create_workbench_record({"客户 REF NO.": "ABC-2", "商机编号": "X-9", "货物数据": "1件"}, ROLE))

    _, _, body = fake.sent[0]
    assert body["fields"] == {"客户 REF NO.": "ABC-2"}


def test_值按飞书类型归一化(fake: FakeCreate) -> None:
    run(
        fake.create_workbench_record(
            {"货好时间": "2026-10-09 08:00:00", "是否拿到货": True, "业务类型": "空运"},
            ROLE,
        )
    )

    _, _, body = fake.sent[0]
    fields = body["fields"]
    assert fields["业务类型"] == "空运"
    assert fields["是否拿到货"] is True
    # 日期要变成毫秒时间戳，原样送字符串飞书会拒
    assert isinstance(fields["货好时间"], int)


def test_一项都写不了时报错_而不是建一条空记录(fake: FakeCreate) -> None:
    """空记录在飞书里就是个改不动的孤儿行，宁可让用户先把表填上。"""
    with pytest.raises(CustomException, match="没有可写入的字段"):
        run(fake.create_workbench_record({"商机编号": "X-1", "货物数据": "1件"}, ROLE))

    assert fake.sent == []


def test_字段全空同样拦下(fake: FakeCreate) -> None:
    with pytest.raises(CustomException, match="没有可写入的字段"):
        run(fake.create_workbench_record({}, ROLE))

    assert fake.sent == []


def test_飞书没回_record_id_时算失败(fake: FakeCreate) -> None:
    """没 record_id 就没法定位这条记录（改不了也删不了），不能当成功放过去。"""
    fake.response = {"data": {}}

    with pytest.raises(CustomException, match="未返回记录 ID"):
        run(fake.create_workbench_record({"客户 REF NO.": "ABC-3"}, ROLE))


def test_未知工作台被拦下(fake: FakeCreate) -> None:
    with pytest.raises(CustomException, match="未知工作台"):
        run(fake.create_workbench_record({"客户 REF NO.": "ABC-4"}, "no_such_role"))


# ============================ 新建表单（读飞书表单视图） ============================


def _field(field_id: str, name: str, ftype: int, **extra: Any) -> dict[str, Any]:
    return {"field_id": field_id, "field_name": name, "type": ftype, "ui_type": "", **extra}


class FakeForm(FakeCreate):
    """能应答表单视图与关联表两张表的路由版桩。"""

    def __init__(self) -> None:
        super().__init__({})
        # 表 id -> 原始字段定义
        self.tables: dict[str, list[dict[str, Any]]] = {"": []}
        # 表 id -> 记录
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.form_fields: list[dict[str, Any]] = []
        self.paths: list[str] = []
        # (表 id, 投影的列)，用来验证候选列表只向飞书要主字段那一列
        self.projections: list[tuple[str, tuple[str, ...]]] = []
        # 实时翻过 ``/fields`` 的**表 id**。`_link_target_table` 每次展开关联分区都要
        # 定位一次目标表，翻本表字段定义（``""``）既没必要又是两页 0.7 秒，用它钉住
        # 「这回问的是缓存过的字段元数据」。目标表自己要的字段定义不算在内。
        self.raw_field_tables: list[str] = []

    async def get_fields(self) -> list[Any]:
        # 走**生产那份**转换函数，不在这里手抄一遍：手抄的版本一开始漏了 ``link_table_id``，
        # 于是 `_link_target_table` 拿到空表 id 就报「没有指向任何表」，而这跟被测逻辑
        # 毫无关系。少抄一份就少一处会悄悄跑偏的平行实现。
        return service_module._metas_from_raw(self.tables[""])

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        self.raw_field_tables.append(table_id)
        return self.tables.get(table_id, [])

    async def _list_raw_records(
        self, table_id: str, limit: int, field_names: list[str] | None = None
    ) -> list[dict[str, Any]]:
        # 记下「向飞书要了哪几列」：候选列表必须只投影主字段（理由见下面的用例）
        self.projections.append((table_id, tuple(field_names or ())))
        return self.records.get(table_id, [])[:limit]

    async def call(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        self.paths.append(url)
        if "/forms/" in url:
            return {"data": {"items": self.form_fields, "has_more": False}}
        return {"data": {}}


@pytest.fixture
def fake_form(monkeypatch: pytest.MonkeyPatch) -> FakeForm:
    instance = FakeForm()
    instance.tables[""] = [
        _field("fld_a", "客户", 21, property={"table_id": "tbl_cust"}),
        _field("fld_b", "件数", 2),
        _field("fld_c", "目的 港", 4, property={"options": [{"name": "釜山"}]}),
        _field("fld_d", "负责航线", 11, property={"multiple": True}),
        _field("fld_e", "目的港", 19),
        _field("fld_f", "商机负责人", 11, property={"multiple": False}),
    ]
    instance.tables["tbl_cust"] = [
        _field("fld_p", "客商信息", 1, is_primary=True),
        _field("fld_q", "客商全称", 1),
    ]
    instance.records["tbl_cust"] = [
        {"record_id": "rec_1", "fields": {"客商信息": "英联模具", "客商全称": "青岛英联精密模具有限公司"}},
        {"record_id": "rec_2", "fields": {"客商信息": "海信模具", "客商全称": "青岛海信精密模具有限公司"}},
        {"record_id": "rec_3", "fields": {"客商信息": "欧陆", "客商全称": "欧陆目的港服务有限公司"}},
    ]

    # 表单用例只关心表单本身；默认人员那套会连带去拉通讯录，单独用例再开
    use_config(monkeypatch, user_defaults={})

    async def fake_http(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        return await instance.call(method, url, token, **kwargs)

    monkeypatch.setattr(service_module, "_http_json", fake_http)
    return instance


def test_表单字段_照飞书表单的可见字段与顺序(fake_form: FakeForm) -> None:
    fake_form.form_fields = [
        {"field_id": "fld_a", "title": "客户", "required": True, "visible": True},
        {"field_id": "fld_b", "title": "件数", "required": False, "visible": True},
        {"field_id": "fld_e", "title": "目的港", "required": False, "visible": True},
    ]

    form = run(fake_form.get_create_form(ROLE))

    assert [f["name"] for f in form] == ["客户", "件数", "目的港"]
    assert [f["control"] for f in form] == ["link", "number", "readonly"]
    assert form[0]["required"] is True


def test_表单里不可见的字段不出现(fake_form: FakeForm) -> None:
    """表单有 32 个条目，绝大部分 visible=false（飞书不会删，只是隐藏）。"""
    fake_form.form_fields = [
        {"field_id": "fld_b", "title": "件数", "visible": True},
        {"field_id": "fld_d", "title": "负责航线", "visible": False},
    ]

    assert [f["name"] for f in run(fake_form.get_create_form(ROLE))] == ["件数"]


def test_表单标题与字段名分开_提交要用字段名(fake_form: FakeForm) -> None:
    """实测商机表里表单标题写「目的港」，字段其实叫「目的 港」（带空格）。

    按标题当 key 就会写到一个不存在的字段上——这正是本接口按 field_id 反查字段名的原因。
    """
    fake_form.form_fields = [{"field_id": "fld_c", "title": "目的港", "visible": True}]

    field = run(fake_form.get_create_form(ROLE))[0]

    assert field["title"] == "目的港"
    assert field["name"] == "目的 港"


def test_表单里指向已删字段的条目被跳过(fake_form: FakeForm) -> None:
    fake_form.form_fields = [
        {"field_id": "fld_b", "title": "件数", "visible": True},
        {"field_id": "fld_gone", "title": "早删了的字段", "visible": True},
    ]

    assert [f["name"] for f in run(fake_form.get_create_form(ROLE))] == ["件数"]


def test_没配表单视图的工作台返回空(fake_form: FakeForm) -> None:
    """富士康那套没有表单视图，应回落到空而不是报错。"""
    assert run(fake_form.get_create_form("customer_service")) == []


def test_关联字段能列出目标表的候选记录(fake_form: FakeForm) -> None:
    options = run(fake_form.list_link_options("客户", role=ROLE))

    assert options == [
        {"record_id": "rec_1", "text": "英联模具"},
        {"record_id": "rec_2", "text": "海信模具"},
        {"record_id": "rec_3", "text": "欧陆"},
    ]


def test_候选记录按主字段文本过滤_不区分大小写(fake_form: FakeForm) -> None:
    assert [o["record_id"] for o in run(fake_form.list_link_options("客户", search="模具", role=ROLE))] == [
        "rec_1",
        "rec_2",
    ]


def test_候选列表只向飞书要主字段那一列(fake_form: FakeForm) -> None:
    """「新建商机」点一次「客户」就要扫一遍客商表，而整表全列是个**两位数秒**的操作。

    实测同一张客商表（193 行 / 15 列）：整表全列 20.85s，`page_size=100` 那档直接 30s 超时；
    只投影主字段 0.97s。前端 axios 超时 15s——超时它就不等了，用户看到的是「新建商机一直
    转圈」或「没数据，刷新一下才有」。投影省的不只是流量：客商表里有 3 列是公式/查找/双向
    关联，飞书要为每一行现算它们，主字段本身就是个公式。
    """
    run(fake_form.list_link_options("客户", role=ROLE))

    assert fake_form.projections == [("tbl_cust", ("客商信息",))]


def test_要按别的列筛时_把那一列也投影上(fake_form: FakeForm) -> None:
    """过滤是**在本地做**的（`_value_in` 读的是记录里那一列的值），漏投影就会误筛掉所有候选。"""
    fake_form.tables["tbl_cust"] = [
        _field("fld_p", "客商信息", 1, is_primary=True),
        _field("fld_r", "客商角色", 4),
    ]
    fake_form.records["tbl_cust"] = [
        {"record_id": "rec_1", "fields": {"客商信息": "欧陆", "客商角色": ["订舱代理"]}},
    ]

    opts = run(
        fake_form.list_link_options("客户", role=ROLE, filter_field="客商角色", filter_values=["订舱代理"])
    )

    assert fake_form.projections == [("tbl_cust", ("客商信息", "客商角色"))]
    assert [o["record_id"] for o in opts] == ["rec_1"]


def test_非关联字段没有候选记录(fake_form: FakeForm) -> None:
    with pytest.raises(CustomException, match="不是关联字段"):
        run(fake_form.list_link_options("件数", role=ROLE))


def test_找目标表时走缓存字段元数据_不实时翻字段定义(fake_form: FakeForm) -> None:
    """`_link_target_table` 每次展开关联分区都会调一次，而实时翻 ``/fields``
    要两页、实测 0.65~0.76 秒——占了一个关联 Tab（~1.6s）的小一半。

    目标表 id 在缓存过的字段元数据里已经有一份（``FieldMetaSchema.link_table_id``），
    没有任何理由再回本表的原始定义里翻。改回 `_raw_field_items` 这条用例就会红。

    只钉**本表**（``""``）：目标表 ``tbl_cust`` 自己的字段定义还是要拉的，它跟这次
    优化无关（本桩没有 Redis，走的就是实时那条路）。
    """
    run(fake_form.list_link_options("客户", role=ROLE))

    assert "" not in fake_form.raw_field_tables


def test_关联字段不存在时报错(fake_form: FakeForm) -> None:
    with pytest.raises(CustomException, match="不存在字段"):
        run(fake_form.list_link_options("查无此字段", role=ROLE))


def test_关联字段没有目标表时报错(fake_form: FakeForm) -> None:
    fake_form.tables[""] = [_field("fld_a", "客户", 21, property={})]

    with pytest.raises(CustomException, match="没有指向任何表"):
        run(fake_form.list_link_options("客户", role=ROLE))


@pytest.mark.parametrize(
    ("field_type", "control"),
    [
        (1, "text"),
        (13, "text"),
        (2, "number"),
        (3, "single"),
        (4, "multi"),
        (5, "date"),
        (7, "checkbox"),
        (17, "attachment"),
        (18, "link"),
        (21, "link"),
        (11, "user"),  # 人员：值来自通讯录
        (19, "readonly"),  # 查找引用：由飞书算
        (20, "readonly"),  # 公式
        (1005, "readonly"),  # 自动编号
        (3001, "readonly"),  # 按钮
    ],
)
def test_字段类型到控件的映射(field_type: int, control: str) -> None:
    """新建表单挂什么控件只在这里定义一次，前端别再自己从 type 推一遍。"""
    assert service_module._form_control(field_type) == control


# ============================ 人员字段（写得起、选得到） ============================


def test_人员字段_写飞书要转成_id_数组() -> None:
    """飞书只认 open_id，送姓名会被整单拒掉；且人员字段的值**永远是数组**。"""
    assert service_module.denormalize_field_value(11, "ou_a") == [{"id": "ou_a"}]
    assert service_module.denormalize_field_value(11, ["ou_a", "ou_b"]) == [{"id": "ou_a"}, {"id": "ou_b"}]
    # 已经是 ``[{"id": ...}]`` 形态的（来回一趟再提交）要能原样过
    assert service_module.denormalize_field_value(11, [{"id": "ou_a"}]) == [{"id": "ou_a"}]
    assert service_module.denormalize_field_value(11, None) is None
    # 清空选择器＝把字段清空，不是发个空串
    assert service_module.denormalize_field_value(11, []) == []


def test_人员字段跟着表字段走_不再当只读(fake_form: FakeForm) -> None:
    """人员字段得能在本系统选：飞书网页里除了它自己的选人浮层没别处可填。"""
    fake_form.form_fields = [
        {"field_id": "fld_d", "title": "负责航线", "required": True, "visible": True},
        {"field_id": "fld_f", "title": "商机负责人", "required": False, "visible": True},
    ]

    form = run(fake_form.get_create_form(ROLE))

    assert [f["control"] for f in form] == ["user", "user"]
    assert form[0]["editable"] is True
    # 能不能多选由飞书字段属性决定：本表「负责航线」multiple=true、「商机负责人」false。
    # 一律放开的话，单选字段被选两个人会被飞书整单拒掉。
    assert form[0]["multiple"] is True
    assert form[1]["multiple"] is False


def test_人员字段写进_payload_走_id_数组(fake: FakeCreate) -> None:
    """端到端：前端选完 open_id，落到飞书请求体里得是 ``[{"id": ...}]``。"""
    fake.types["负责航线"] = 11

    run(fake.create_workbench_record({"客户 REF NO.": "ABC-5", "负责航线": ["ou_a", "ou_b"]}, ROLE))

    _, _, body = fake.sent[0]
    assert body["fields"]["负责航线"] == [{"id": "ou_a"}, {"id": "ou_b"}]


class FakeRedis:
    """通讯录缓存只用到 get/set，别为此拖一个真 Redis 进来。"""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, expire: int | None = None) -> None:
        self.store[key] = value


class FakeContact(FakeForm):
    """按 URL 路由应答通讯录接口的桩；表单与字段那几条走父类。

    继承 ``FakeForm`` 而不是 ``FakeCreate``：默认值用例要同时用上「表单视图」和
    「通讯录」两套应答，两个 fixture 各patch一次 ``_http_json`` 谁后上谁赢，
    用例会看 pytest 的装配顺序决定成败。一个桩管两件事，就没有这个顺序问题。
    """

    def __init__(self) -> None:
        super().__init__()
        # 部门 id -> 子部门 id 列表
        self.children: dict[str, list[str]] = {"0": []}
        # 部门 id -> 成员
        self.members: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[str] = []

    async def call(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(url)
        if "/departments/" in url:
            dept = url.split("/departments/")[1].split("/")[0]
            items = [{"open_department_id": d} for d in self.children.get(dept, [])]
            return {"code": 0, "data": {"items": items, "has_more": False}}
        if "/contact/v3/users" in url:
            dept = (kwargs.get("params") or {}).get("department_id", "")
            return {"code": 0, "data": {"items": self.members.get(dept, []), "has_more": False}}
        return await super().call(method, url, token, **kwargs)


@pytest.fixture
def fake_contact(monkeypatch: pytest.MonkeyPatch) -> FakeContact:
    instance = FakeContact()
    # 默认值用例要下发人员字段，得让表单视图认得出「负责航线」
    instance.tables[""] = [
        _field("fld_b", "件数", 2),
        _field("fld_d", "负责航线", 11, property={"multiple": True}),
    ]
    # 根部门直属 1 人，其余在子部门里——正是威达的真实形状，
    # 只取根部门会得到几乎空的下拉
    instance.children = {"0": ["od_tao"], "od_tao": []}
    instance.members = {
        "0": [{"open_id": "ou_jeff", "name": "Jeff Nie"}],
        "od_tao": [
            {"open_id": "ou_fairy", "name": "Fairy Li", "en_name": "Fairy"},
            {"open_id": "ou_kevin", "name": "Kevin Qin"},
            {"open_id": "ou_anon", "name": "", "en_name": "Anonymous"},
        ],
    }
    redis = FakeRedis()
    use_config(monkeypatch, hidden_users=(), user_defaults={})
    monkeypatch.setattr(service_module, "RedisCURD", lambda _r: redis)
    monkeypatch.setattr(service_module, "_require_credentials", lambda: ("cli_test", "secret", "https://x.invalid"))
    monkeypatch.setattr(service_module, "_open_api", lambda path: f"https://x.invalid{path}")

    async def fake_http(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        return await instance.call(method, url, token, **kwargs)

    monkeypatch.setattr(service_module, "_http_json", fake_http)
    return instance


def test_人员候选_要递归子部门(fake_contact: FakeContact) -> None:
    """飞书成员列表只返回直属成员；不递归就只剩根部门那一个人。"""
    assert run(fake_contact.list_user_options(role=ROLE)) == [
        {"open_id": "ou_anon", "name": "Anonymous"},
        {"open_id": "ou_fairy", "name": "Fairy Li"},
        {"open_id": "ou_jeff", "name": "Jeff Nie"},
        {"open_id": "ou_kevin", "name": "Kevin Qin"},
    ]


def test_人员候选_没中文名时用英文名兜底(fake_contact: FakeContact) -> None:
    names = [u["name"] for u in run(fake_contact.list_user_options(role=ROLE))]

    assert "Anonymous" in names
    assert "" not in names  # 空白选项在下拉里看不出是谁


def test_人员候选_按姓名过滤_不区分大小写(fake_contact: FakeContact) -> None:
    got = run(fake_contact.list_user_options(search="fairy", role=ROLE))

    assert got == [{"open_id": "ou_fairy", "name": "Fairy Li"}]


def test_人员候选_走缓存_不重复爬组织树(fake_contact: FakeContact) -> None:
    """整租户拉一次要爬好几个部门，每开一次新建表单都重爬太浪费。"""
    run(fake_contact.list_user_options(role=ROLE))
    first = len(fake_contact.calls)

    run(fake_contact.list_user_options(role=ROLE))

    assert len(fake_contact.calls) == first
    # 缓存里也得能筛：第二次带关键字不该重新拉
    assert run(fake_contact.list_user_options(search="kevin", role=ROLE)) == [
        {"open_id": "ou_kevin", "name": "Kevin Qin"}
    ]
    assert len(fake_contact.calls) == first


def test_未知工作台_列人员也拦下(fake_contact: FakeContact) -> None:
    with pytest.raises(CustomException, match="未知工作台"):
        run(fake_contact.list_user_options(role="no_such_role"))


def test_排除名单里的人在本地滤掉(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    """通讯录里有账号但不参与本业务的人，不该出现在下拉里（飞书那边删不得）。"""
    use_config(monkeypatch, hidden_users=("Jeff Nie", "Anonymous"))

    names = [u["name"] for u in run(fake_contact.list_user_options(role=ROLE))]

    assert names == ["Fairy Li", "Kevin Qin"]


def test_排除名单与关键字叠加(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    use_config(monkeypatch, hidden_users=("Jeff Nie",))

    # 关键字过滤在排除之后做：被排除的人搜也搜不出来
    assert run(fake_contact.list_user_options(search="jeff", role=ROLE)) == []
    assert [u["name"] for u in run(fake_contact.list_user_options(search="li", role=ROLE))] == ["Fairy Li"]


def test_排除名单不影响缓存里的完整通讯录(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    """通讯录缓存是租户级的，两个 base 共用——把某张表的排除名单写进缓存会污染另一个 base。"""
    use_config(monkeypatch, hidden_users=("Jeff Nie",))
    run(fake_contact.list_user_options(role=ROLE))
    calls = len(fake_contact.calls)

    # 换个不带排除名单的配置再读，缓存里的 Jeff Nie 必须还在
    use_config(monkeypatch, hidden_users=())
    names = [u["name"] for u in run(fake_contact.list_user_options(role=ROLE))]

    assert "Jeff Nie" in names
    assert len(fake_contact.calls) == calls  # 而且没重新爬一遍组织树


def test_新建表单下发人员默认值_姓名换成_open_id(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置里写姓名（给人维护），下发给控件的必须是 open_id（控件认的）。"""
    use_config(monkeypatch, user_defaults={"负责航线": ("Fairy Li", "Kevin Qin")})
    fake_contact.form_fields = [{"field_id": "fld_d", "title": "负责航线", "visible": True}]

    field = run(fake_contact.get_create_form(ROLE))[0]

    assert field["control"] == "user"
    assert field["default"] == ["ou_fairy", "ou_kevin"]


def test_没有默认值的字段下发空数组(fake_contact: FakeContact) -> None:
    fake_contact.form_fields = [{"field_id": "fld_b", "title": "件数", "visible": True}]

    assert run(fake_contact.get_create_form(ROLE))[0]["default"] == []


def test_默认值里换了名或离职的人被跳过(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置是按姓名写的，人一改名就对不上了——跳过，但别把能换到的那部分也丢了。"""
    use_config(monkeypatch, user_defaults={"负责航线": ("Fairy Li", "早就离职的人")})
    fake_contact.form_fields = [{"field_id": "fld_d", "title": "负责航线", "visible": True}]

    assert run(fake_contact.get_create_form(ROLE))[0]["default"] == ["ou_fairy"]


def test_默认值不能绕开排除名单(fake_contact: FakeContact, monkeypatch: pytest.MonkeyPatch) -> None:
    """被排除的人连默认值也当不上——否则会出现「下拉里选不到他，新建出来的记录里却全是他」。"""
    use_config(
        monkeypatch,
        user_defaults={"负责航线": ("Jeff Nie", "Kevin Qin")},
        hidden_users=("Jeff Nie",),
    )
    fake_contact.form_fields = [{"field_id": "fld_d", "title": "负责航线", "visible": True}]

    assert run(fake_contact.get_create_form(ROLE))[0]["default"] == ["ou_kevin"]


# ── 缓存：同一份内容不再每次点开都重算一遍 ────────────────────────────────
#
# 「新建商机」点开一次要整套拉：表单定义翻页取、候选记录整表扫（客商表 193 行，实测
# 整表全列 20.85s）。关掉再点开，同样的内容还是二十秒 —— 用户看到的就是「卡二十秒」。
# 这两样都进了 Redis，时长取最短的 ``_FORM_CACHE_TTL`` / ``_LINK_OPTION_CACHE_TTL``。
# 下面钉的是**缓存真的生效**与**关键字筛选没被缓存吃掉**。


class CountingRedis:
    """够用的内存 Redis：记下写进去的键、值与 TTL，读按原样回。

    真 Redis 由 ``RedisCURD`` 包着（异常一律吞掉），这里只实现它用到的那几个方法。
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, name: str, value: bytes | str, ex: int | None = None) -> bool:
        self.store[name] = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        self.expires[name] = ex
        return True


def use_fake_redis(target: Any, redis: CountingRedis | None = None) -> CountingRedis:
    """把桩的 Redis 换成内存版并交回来。

    ``RedisCURD`` 只用到 ``get``/``set`` 两个方法，所以内存版够用；类型上仍按真 Redis
    断言——``FeishuBitableService._redis`` 声明的是 ``Redis``。
    """
    fake_redis = redis or CountingRedis()
    target._redis = cast("Redis", fake_redis)
    return fake_redis


def test_新建表单第二次读走缓存_不再打飞书(fake_form: FakeForm) -> None:
    fake_form.form_fields = [{"field_id": "fld_b", "title": "件数", "visible": True}]
    redis = use_fake_redis(fake_form)

    first = run(fake_form.get_create_form(ROLE))
    paths_after_first = list(fake_form.paths)

    assert run(fake_form.get_create_form(ROLE)) == first
    assert fake_form.paths == paths_after_first, "第二次不该再发票单请求"
    assert redis.expires[fake_form._form_cache_key(ROLE)] == service_module._FORM_CACHE_TTL


def test_候选记录第二次读走缓存_关键字仍在本地筛(fake_form: FakeForm) -> None:
    """缓存的是**未筛过**的那一份：换关键字只换内存里的筛，不该再扫一遍整表，
    更不该把筛选条件丢掉（丢掉就是下拉里混进不相关的客户）。"""
    redis = use_fake_redis(fake_form)

    first = run(fake_form.list_link_options("客户", role=ROLE))
    projections_after_first = list(fake_form.projections)

    narrowed = run(fake_form.list_link_options("客户", search="欧陆", role=ROLE))

    assert [o["record_id"] for o in narrowed] == ["rec_3"]
    assert first != narrowed
    assert fake_form.projections == projections_after_first, "筛关键字不该重新扫表"
    # 只认候选缓存那个键：同一次调用还会写字段元数据缓存（``_FIELDS_CACHE_TTL`` 600s）
    key = fake_form._link_option_cache_key(fake_form._table_id, "客户", "", None)
    assert redis.expires[key] == service_module._LINK_OPTION_CACHE_TTL


def test_缓存时长取最短的一档(fake_form: FakeForm) -> None:
    """这两个缓存是**拿新鲜度换速度**的：原来刻意不缓存，怕「刚在飞书里建的客户选不到」。

    时长必须明显短于展示用文本那档（``_LINK_TEXT_TTL`` 300s）——5 分钟的滞后和 5 分钟的
    卡顿一样不能接受。改大了这条会红。
    """
    assert service_module._FORM_CACHE_TTL < service_module._LINK_TEXT_TTL
    assert service_module._LINK_OPTION_CACHE_TTL < service_module._LINK_TEXT_TTL


def test_筛选条件不同的候选不共用缓存(fake_form: FakeForm) -> None:
    """「只要订舱代理」与「全要」是两个不同的下拉，混一份缓存会把筛选条件也混掉。"""
    plain = fake_form._link_option_cache_key("tbl_cust", "客户", "", None)
    filtered = fake_form._link_option_cache_key("tbl_cust", "客户", "客商角色", ["订舱代理"])

    assert plain != filtered
    assert fake_form._link_option_cache_key("tbl_cust", "客户", "", []) == plain


def test_缓存内容损坏时退回实拉(fake_form: FakeForm) -> None:
    """缓存里躺的是别的东西（老版本写进去的、被人工改过）时只该多拉一次，不该整页报错。"""
    redis = CountingRedis()
    use_fake_redis(fake_form, redis)
    redis.store[fake_form._link_option_cache_key(fake_form._table_id, "客户", "", None)] = "{这不是 JSON"

    options = run(fake_form.list_link_options("客户", role=ROLE))

    assert [o["record_id"] for o in options] == ["rec_1", "rec_2", "rec_3"]
    assert fake_form.projections, "损坏的缓存要触发一次真实拉取"
