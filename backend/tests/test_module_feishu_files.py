"""附件（上传 / 下载 / 预览所需元数据）的回归测试。

三个**只有真实调用才会暴露**的坑，这里都钉死：

1. 下载响应头里的文件名几乎都是中文，而 HTTP 头只能写 latin-1。直接把中文放进
   ``filename="..."`` 会在 Starlette 编码响应头时抛 ``UnicodeEncodeError``，
   表现为「下载接口 500」——本仓库历史上已有过同类（``FileResponse(method=...)``）事故。
2. 飞书下载出错时返回的是 **JSON 错误体**，HTTP 状态还可能是 200。不做判断就转发，
   浏览器会存下一个内容是错误 JSON 的「坏文件」，用户看到的是「下载成功但打不开」。
3. **多维表格附件下载必须带 ``extra={"bitablePerm": {...}}``**。不带它飞书返回 ``400``
   且**响应体为空**（换 file_token 也一样），带上才 200。这一条是真实调用才发现的——
   当时的实现、ruff、pyright 全绿，一调真接口就 400。见
   ``test_download_media_sends_bitable_perm_extra``。

HTTP 层是假的：``settings`` 换成一次性替身、``_token`` 返回占位串、``_get_client`` 换成
内存假客户端，**不读真实凭证、不发任何外部请求**。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

import pytest
from redis.asyncio.client import Redis

from app.plugin.module_feishu import service as svc

FAKE_SETTINGS = SimpleNamespace(
    LARK_APP_ID="cli_fake",
    LARK_APP_SECRET="fake-secret",
    LARK_APP_TOKEN="app_fake",
    LARK_TABLE_ID="tbl_fake",
    LARK_BASE_URL="https://open.feishu.cn/open-apis",
    LARK_HTTP_TIMEOUT=30.0,
)


class FakeResponse:
    """够用的 httpx.Response 替身。"""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        """保持与 httpx 一致：4xx/5xx 抛错，2xx 静默通过。"""
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, get_response: FakeResponse, request_response: FakeResponse) -> None:
        self.get_response = get_response
        self.request_response = request_response
        self.gets: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.gets.append({"url": url, **kwargs})
        return self.get_response

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.request_response


class FakeService(svc.FeishuBitableService):
    """``_token`` 直接返回占位串，避免走 Redis/真实换 token 链路。"""

    async def _token(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return "fake-tenant-token"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _async(fn: Any) -> Any:
    """把普通函数包成协程函数，用来替换 service 上的 async 方法。"""

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """把凭证与 HTTP 客户端都换成假实现，返回 ``(service, client)``。

    两个响应参数是**不同**的通道，别搞混（搞混的话错误码用例会静默走成功分支）：
    ``get_response`` 供 ``download_media`` 用，``request_response`` 供 ``upload_media`` 用。
    """

    def _install(get_response: FakeResponse, request_response: FakeResponse | None = None):
        client = FakeClient(get_response, request_response or FakeResponse(payload={"code": 0}))
        monkeypatch.setattr(svc, "settings", FAKE_SETTINGS)
        monkeypatch.setattr(svc, "_get_client", lambda: client)
        return FakeService(cast_redis()), client

    return _install


def cast_redis() -> Redis:
    return Redis.from_url("redis://127.0.0.1:1/0")  # 只用于构造，不会被真正使用


# ------------------------------------------------------------------
#  Content-Disposition：中文文件名不得让响应头编码崩掉
# ------------------------------------------------------------------


def test_content_disposition_is_latin1_safe_for_chinese_name() -> None:
    header = svc._content_disposition("订舱单-4100110320.pdf")

    # 关键：响应头必须能按 latin-1 编码，否则 Starlette 发响应时直接抛 UnicodeEncodeError
    header.encode("latin-1")
    assert "filename*=UTF-8''" in header
    assert "%E8%AE%A2" in header  # 「订」的 UTF-8 百分号编码


def test_content_disposition_keeps_plain_ascii_name_readable() -> None:
    header = svc._content_disposition("invoice.pdf")

    assert 'filename="invoice.pdf"' in header
    header.encode("latin-1")


def test_content_disposition_escapes_quotes_and_backslashes() -> None:
    header = svc._content_disposition('a"b\\c.pdf')

    fallback = header.split('filename="')[1].split('"')[0]
    assert '"' not in fallback
    assert "\\" not in fallback


def test_filename_from_disposition_prefers_rfc5987() -> None:
    assert svc._filename_from_disposition("attachment; filename*=UTF-8''%E8%AE%A2%E5%8D%95.pdf") == "订单.pdf"
    assert svc._filename_from_disposition('attachment; filename="a.pdf"') == "a.pdf"
    assert svc._filename_from_disposition("attachment") == ""
    assert svc._filename_from_disposition("") == ""


# ------------------------------------------------------------------
#  附件字段的归一化 / 还原
# ------------------------------------------------------------------


def test_normalize_attachment_drops_token_bearing_urls() -> None:
    """送给前端的附件对象里不能带 url / tmp_url —— 那是含临时凭证的地址。"""
    raw = [
        {
            "file_token": "ft1",
            "name": "订舱单.pdf",
            "size": 1234,
            "type": "application/pdf",
            "url": "https://open.feishu.cn/open-apis/drive/v1/medias/ft1/download?extra=SECRET",
            "tmp_url": "https://open.feishu.cn/open-apis/drive/v1/medias/batch_get_tmp_download_url?SECRET",
        }
    ]

    out = svc.normalize_field_value(17, raw)

    assert out == [{"file_token": "ft1", "name": "订舱单.pdf", "size": 1234, "type": "application/pdf"}]
    assert "url" not in out[0]
    assert "tmp_url" not in out[0]


def test_denormalize_attachment_writes_only_file_tokens() -> None:
    """写入只认 file_token；多余字段（url/name/size）一并去掉。"""
    assert svc.denormalize_field_value(17, [{"file_token": "ft1", "name": "a.pdf", "url": "x"}]) == [
        {"file_token": "ft1"}
    ]
    assert svc.denormalize_field_value(17, ["ft1"]) == [{"file_token": "ft1"}]
    assert svc.denormalize_field_value(17, [{"name": "没有 token"}]) == []
    assert svc.denormalize_field_value(17, []) == []
    assert svc.denormalize_field_value(17, "not-a-list") == []
    assert svc.denormalize_field_value(17, None) is None


def test_attachment_field_is_editable() -> None:
    """17 必须在可编辑类型里，否则 `_prepare_write` 会把上传结果静默丢掉。"""
    assert 17 in svc.EDITABLE_TYPES


# ------------------------------------------------------------------
#  下载
# ------------------------------------------------------------------


def test_download_media_returns_bytes_name_and_type(patched) -> None:
    service, client = patched(
        FakeResponse(
            content=b"%PDF-1.4 fake",
            headers={
                "content-type": "application/pdf",
                "content-disposition": "attachment; filename*=UTF-8''%E8%AE%A2%E5%8D%95.pdf",
            },
        )
    )

    content, name, media_type = run(service.download_media("ft1"))

    assert content == b"%PDF-1.4 fake"
    assert name == "订单.pdf"
    assert media_type == "application/pdf"
    assert client.gets[0]["url"].endswith("/drive/v1/medias/ft1/download")
    assert client.gets[0]["headers"]["Authorization"] == "Bearer fake-tenant-token"


def test_download_media_sends_bitable_perm_extra(patched) -> None:
    """裸请求真实接口返回 400 空响应体；extra.bitablePerm 是必需的。"""
    service, client = patched(FakeResponse(content=b"x", headers={"content-type": "application/pdf"}))

    run(service.download_media("ft1"))

    extra = json.loads(client.gets[0]["params"]["extra"])
    assert extra["bitablePerm"]["tableId"] == "tbl_fake"
    assert "rev" in extra["bitablePerm"]


def test_download_media_prefers_explicit_table_id(patched) -> None:
    """关联表的附件必须用**那张表**的 tableId 去换权限。

    实测：商机自己的 49 个字段里没有任何附件字段，真实附件全在「对客方案」「航线报价」
    这类关联表里；拿主表的 id 去下会得到飞书 ``403`` 空响应体。
    """
    service, client = patched(FakeResponse(content=b"x", headers={"content-type": "application/pdf"}))

    run(service.download_media("ft1", "tbl_other"))

    extra = json.loads(client.gets[0]["params"]["extra"])
    assert extra["bitablePerm"]["tableId"] == "tbl_other"


def test_download_media_falls_back_to_own_table(patched) -> None:
    """不传 table_id 时仍用本工作台的表——富士康那套页面不传，行为不能变。"""
    service, client = patched(FakeResponse(content=b"x", headers={"content-type": "application/pdf"}))

    run(service.download_media("ft1"))

    extra = json.loads(client.gets[0]["params"]["extra"])
    assert extra["bitablePerm"]["tableId"] == "tbl_fake"


def test_linked_records_returns_table_id(patched, monkeypatch: pytest.MonkeyPatch) -> None:
    """`linked_records` 要带上关联表 id：前端下载那张表上的附件只能从这里知道它。"""
    service, _client = patched(FakeResponse(content=b"x"))
    # `_link_target_table` 现在多收一个「字段长在哪张表上」（二级关联用，见对客方案费用明细）
    monkeypatch.setattr(service, "_link_target_table", _async(lambda _name, _table_id="": "tbl_linked"))
    monkeypatch.setattr(service, "_table_fields", _async(lambda _tid: []))
    monkeypatch.setattr(service, "_search_page", _async(lambda *a, **kw: {"items": [], "has_more": False}))

    result = run(service.linked_records("对客方案", ["rec1"], "wd_customer_service"))

    assert result["table_id"] == "tbl_linked"


def test_download_media_rejects_empty_token(patched) -> None:
    service, _client = patched(FakeResponse())

    with pytest.raises(svc.CustomException):
        run(service.download_media(""))


def test_download_media_raises_readable_error_on_http_failure(patched) -> None:
    """99991661（Missing access token）必须变成可读文案，而不是把错误体当文件吐出去。"""
    service, _client = patched(
        FakeResponse(status_code=403, payload={"code": 99991661, "msg": "Missing access token"})
    )

    with pytest.raises(svc.CustomException) as exc:
        run(service.download_media("ft1"))

    assert "99991661" in exc.value.msg
    assert "Missing access token" in exc.value.msg


def test_download_media_rejects_200_json_body(patched) -> None:
    """HTTP 200 但内容是 JSON —— 这正是「下载成功却打不开」的来源，必须拦下。"""
    service, _client = patched(
        FakeResponse(
            status_code=200,
            payload={"code": 1061002, "msg": "params error"},
            headers={"content-type": "application/json; charset=utf-8"},
        )
    )

    with pytest.raises(svc.CustomException) as exc:
        run(service.download_media("ft1"))

    assert "1061002" in exc.value.msg


# ------------------------------------------------------------------
#  上传
# ------------------------------------------------------------------


def test_upload_media_maps_response_to_attachment_value(patched) -> None:
    service, client = patched(
        FakeResponse(payload={"code": 0, "data": {"file_token": "ft_new"}}),
        FakeResponse(payload={"code": 0, "data": {"file_token": "ft_new"}}),
    )

    result = run(service.upload_media("订舱单.pdf", b"%PDF-1.4", "application/pdf"))

    assert result == {
        "file_token": "ft_new",
        "name": "订舱单.pdf",
        "size": 8,
        "type": "application/pdf",
    }
    body = client.requests[0]
    assert body["url"].endswith("/drive/v1/medias/upload_all")
    # 附件必须先挂到该 base 上，漏了 parent_type/parent_node 拿到的 token 写不进字段
    assert body["data"]["parent_type"] == "bitable_file"
    assert body["data"]["parent_node"] == "app_fake"
    assert body["data"]["size"] == "8"
    assert body["data"]["file_name"] == "订舱单.pdf"
    # 上传的 file_token 不能出现在日志/返回值以外的地方被回写
    assert result["file_token"] == "ft_new"


def test_upload_media_rejects_empty_and_oversize(patched, monkeypatch: pytest.MonkeyPatch) -> None:
    service, client = patched(FakeResponse(payload={"code": 0}))

    with pytest.raises(svc.CustomException):
        run(service.upload_media("", b"x", "text/plain"))
    with pytest.raises(svc.CustomException):
        run(service.upload_media("a.pdf", b"", "application/pdf"))

    # 超限在本地就拦下，不把 20MB 传上去换一个错误码回来
    monkeypatch.setattr(svc, "_MAX_UPLOAD_BYTES", 4)
    with pytest.raises(svc.CustomException):
        run(service.upload_media("a.pdf", b"12345", "application/pdf"))

    assert client.requests == []


def test_upload_media_surfaces_lark_error_code(patched) -> None:
    error = FakeResponse(payload={"code": 1061002, "msg": "params error"})
    service, _client = patched(error, error)

    with pytest.raises(svc.CustomException) as exc:
        run(service.upload_media("a.pdf", b"data", "application/pdf"))

    assert "1061002" in exc.value.msg
