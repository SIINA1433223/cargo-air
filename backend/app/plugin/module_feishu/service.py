"""飞书多维表格（Bitable）服务层。

对外提供字段元数据与记录读写，读写**直接透传飞书**，本地不落库——飞书即唯一
数据源，双向同步天然成立，且保留飞书侧已有的公式/查找/自动编号/按钮等自动化。

飞书原生字段值是异构结构（文本是 ``[{"text":..}]``、人员是 ``[{id,name}..]``、
日期是毫秒时间戳），本层在读写边界做归一化/还原：对外（前端）使用简单类型，
对内（飞书）使用飞书约定格式。

列表查询走 ``records/search``（服务端过滤 + 字段投影 + 游标分页），不再全量拉表；
过滤条件用 ``children`` 嵌套表达「待办 AND 关键字」——飞书不支持把嵌套条件直接写进
``conditions``，必须是 ``children`` 里的子过滤器对象。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, unquote

import httpx
from fastapi import status
from redis.asyncio.client import Redis

from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import log
from app.core.redis_crud import RedisCURD

from .schema import (
    FieldMetaSchema,
    MergeType,
    RelatedBlockSchema,
    WorkbenchConfigSchema,
    WorkbenchGroupSchema,
    WorkbenchSortSchema,
)
from .workbench import (
    CARGO_FIELDS,
    MERGE_HAWB_FIELD,
    MERGE_MASTER_FIELD,
    MERGE_SYNC_FIELDS,
    SINGLE_STATUS_GOT_FIELD,
    SINGLE_STATUS_HANDOVER_FIELD,
    SINGLE_STATUS_NOTE_FIELD,
    SINGLE_STATUS_ORDER_FIELD,
    SINGLE_STATUS_PRICED_FIELD,
    SINGLE_STATUS_PRICED_VALUE,
    SINGLE_STATUS_QUOTE_FIELD,
    RelatedTable,
    WorkbenchConfig,
    WorkbenchGroup,
    detail_field_names,
    get_workbench,
)

# 只读字段类型：公式/查找/自动编号/按钮/创建修改人时间——不渲染编辑控件。
READONLY_TYPES: frozenset[int] = frozenset({19, 20, 1001, 1002, 1003, 1004, 1005, 3001})

# 本系统表单直接支持编辑的字段类型（其余可写类型 MVP 仅展示）。
# 18/21 是单向/双向关联，本期只支持「合并组」这一类关联的写入。
# 17 是附件：值由上传接口产出的 ``file_token`` 组成，写入格式见 `denormalize_field_value`。
# 11 是人员：值由通讯录选人产出，写入格式见 `denormalize_field_value`（只认 open_id）。
EDITABLE_TYPES: frozenset[int] = frozenset({1, 2, 3, 4, 5, 7, 11, 13, 15, 17, 18})

# 飞书 ``drive/v1/medias/upload_all`` 的单文件上限（官方 20MB），超了直接在本地拦下，
# 免得把 20MB 传上去再收一个 1061045。
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 关联字段候选记录的拉取上限。目标表都是几百行的主数据表（客户表 192 行），
# 一次拉回来本地筛足够了；真撞上万行的表再来做服务端检索。
_LINK_OPTION_MAX = 1000

# 展开关联分区时整表拉记录的上限。关联表同样是几十上百行的小表（航线报价 68 行），
# 整表拉一次比按 id 逐条查快一个量级——飞书单取一条要算全部公式/查找（本表实测 3~5 秒）。
_LINKED_RECORD_MAX = 2000

# 关联字段类型（单向/双向）。
LINK_TYPES: frozenset[int] = frozenset({18, 21})

# Stage 字段（飞书多维表格插件面板的「阶段条」，威达订单表上叫 国内客服/操作/航线）。
#
# 它的 ``property`` 恒为 ``null``，且飞书**没有**单字段 GET 端点（实测 404），
# 所以节点里挂了哪些字段**只能靠人给**——别指望从接口推。值也读不出来（飞书不把它当数据列），
# 收进详情分组只会多一格永远空白的只读框，故 `_detail_groups` 一律跳过它。
STAGE_FIELD_TYPE = 24

# 能对关键字做 `contains` 的字段类型：文本 / 电话 / 超链接 / 公式（实测，见 `_build_filter`）。
#
# 实测**不支持**、列进来会让整次搜索报 `1254018 InvalidFilter` 的：自动编号(1005，`contains`
# 与 `is` 都拒——订单编号这种最想搜的列偏偏搜不了，只能靠索引页的客户端筛选碰它)、
# 查找引用(19)、关联(18/21)、单选(3)、按钮(3001)。
# 公式列要留神：这里放行是因为实测 `供应商报价信息` 这类**文本公式**能搜，返回数字/日期的
# 公式列则会被拒——真拒了就把该列从工作台的 search_fields 里去掉，别指望它自己降级。
_SEARCHABLE_TYPES: frozenset[int] = frozenset({1, 13, 15, 20})

# 人员字段类型。飞书里它的值是 ``[{"id": "ou_xxx", "name": ...}]``，
# 写回去只认 id（通讯录成员的 open_id），不认姓名。
USER_TYPE = 11

# 日期字段类型。查找列(19)/公式(20) 的返回值带一层 ``{"type": 源类型, "value": [...]}``，
# 那里面的 ``type`` 是判断「这串数字是不是时间戳」的唯一依据（见 `_unwrap_lookup_value`）。
DATE_TYPE = 5

# 通讯录缓存秒数。人员变动远不如业务数据频繁，而拉一次要把整棵组织树爬一遍
# （并发后仍有 2.5s，见 `_contact_department_ids`）。过期那次会**落在某个真实用户身上**，
# 且正好是他点「新建」的时候，所以这里宁长勿短：新同事晚半小时出现在下拉里没人会疼，
# 但点一次新建等 4.7s 会。
_CONTACT_CACHE_TTL = 1800

# 单次翻页条数与递归部门数上限。上限是防组织树异常深/成环时无限翻页——
# 威达整租户 12 个部门 18 个人，正常远够。
_CONTACT_PAGE_SIZE = 50
_CONTACT_DEPT_MAX = 200

# 爬通讯录时的并发上限。飞书通讯录是**逐部门**取的接口（只返回直属成员），
# 串行爬一次实测 **9.5s**——而这串时间每次都要算进「新建商机」抽屉（`get_create_form`
# 要拿人员字段的默认值）。并发后掉到 1s 上下。
# 不设限不行：`_CONTACT_DEPT_MAX` 是 200，全量并发会在组织树深的租户上触发飞书限流。
_CONTACT_CONCURRENCY = 8

# 详情页分组里出现的字段若不在表结构中（改表后遗留），按只读占位处理，避免整个接口报错。
_UNKNOWN_FIELD = FieldMetaSchema(
    name="", type=0, ui_type="Unknown", read_only=True, editable=False, options=[]
)


def _detail_groups(
    metas: list[FieldMetaSchema], sections: tuple[WorkbenchGroup, ...]
) -> list[WorkbenchGroupSchema]:
    """把详情字段切成若干 Tab。

    ``sections`` 里 ``fields`` 为**空**的那一组是「其余字段」，收走所有没被别的组点名的
    字段——商机那类四五十字段的表，主分组留空即可吃下大头，不必把字段名再抄一遍。
    有多个空组时只有第一个吃到，后者为空（配置写错不该报错，也不该把字段翻倍）。

    每组内的顺序一律**照飞书表里的顺序**（``metas`` 本身就按飞书返回顺序），不看配置里
    写的次序——配置只决定「谁归哪一组」。表里没有的字段名直接丢弃，由调用方记日志。

    Stage 字段（type 24）**整类**不进来：它读不出值、节点也读不出定义，摆进详情只会多一格
    永远空白的只读框。在入口处滤掉，配置里就算点名了也不出（与「表里没有的字段名」同一处理）。
    """
    if not sections:
        return []
    metas = [m for m in metas if m.type != STAGE_FIELD_TYPE]
    named = {name for section in sections for name in section.fields}
    groups: list[WorkbenchGroupSchema] = []
    catch_all_taken = False
    for section in sections:
        if section.fields:
            wanted = set(section.fields)
            picked = [m for m in metas if m.name in wanted]
        elif catch_all_taken:
            # 第二个空组：剩下的已经被上一个空组吃光了。不报错（配置笔误不该让详情页打不开），
            # 但绝不能把同一批字段再发一遍——界面上会变成两份一模一样的格子。
            picked = []
        else:
            catch_all_taken = True
            picked = [m for m in metas if m.name not in named]
        groups.append(WorkbenchGroupSchema(label=section.label, fields=picked))
    return groups


# 字段元数据缓存秒数（表结构基本不变，缓存后可省掉每次操作前的 fields 请求）。
_FIELDS_CACHE_TTL = 600

# 字段元数据缓存格式版本。改了 ``FieldMetaSchema`` 的结构就 +1（理由见 ``_fields_cache_key``）。
_FIELDS_CACHE_VERSION = 4

# 关联字段「已有关联记录的显示文本」缓存时长。
# 比字段元数据（600s）短：字段结构一周未必变一次，而客户/供应商的**名字**随时可能被改，
# 名字又只是个展示标签，宁可多查几次也不要让人看到改名前的老名字。
_LINK_TEXT_TTL = 300

# 「单票状态」用的订单表索引缓存时长（``_order_status_map``）。
#
# 这个映射要整表扫一遍订单表（几十行、投影两列，实测 ~1.3s），而**每一次列表请求**都要它
# ——国内客服与航线价格的列表有一半时间花在这上面。订单表的内容按业务节奏是以天/小时变的，
# 60 秒的滞后无感；改短了省不下什么，改长了（比如跟 ``_LINK_TEXT_TTL`` 一样 300s）才会
# 让人怀疑「我刚在飞书里核了价，列表标签怎么没变」。
#
# 本系统自己写的订单（``_batch_update`` 命中订单表）会**立刻清掉**这个缓存，所以只有
# 「在飞书里改的」才吃这 60 秒。
_ORDER_STATUS_TTL = 60

# 「新建」抽屉那两件读物的缓存时长（``get_create_form`` 的表单定义、``list_link_options``
# 的候选记录）。两者在飞书里都是**慢读**：表单定义要翻页取，候选记录要整表拉（客户表
# 192 行，实测单次最慢 19.6s）——而点开一次「新建商机」就把这两样从头再算一遍，
# 关掉、再点开，同样的内容还是二十秒。
#
# 时长取本文件最短（对照 ``_ORDER_STATUS_TTL`` 60s、``_LINK_TEXT_TTL`` 300s）：这一格
# 真正怕的只有「刚在飞书里新建的客户在下拉里选不到」（原来为此刻意不做缓存，见
# ``list_link_options`` 的注释），30 秒是「连点几次都秒开」与「新增的客户最多晚半分钟
# 出现」之间的折中。想更短就调小这两个数，代价是飞书请求变多。
_FORM_CACHE_TTL = 30
_LINK_OPTION_CACHE_TTL = 30

# 这两个缓存的**结构版本**：改了 ``get_create_form`` / ``list_link_options`` 的返回结构
# 就 +1，让老缓存立刻作废——理由同 ``_FIELDS_CACHE_VERSION``（否则要等 TTL 才自愈，
# 表现为「代码改了，界面半分钟内没变化」）。
_FORM_CACHE_VERSION = 1
_LINK_OPTION_CACHE_VERSION = 1

# 工作台列表页的整页缓存。
#
# 前端一次要看全量（2213 条 = 5 页，每页约 5~6 秒），刷新、切工作台、翻页都会重新打飞书。
# 缓存**整页响应**（压缩后约几百 KB/页），TTL 内重复打开几乎是本地读取。
#
# 失效靠版本号：本系统任何写操作（改记录/合并/拆出/新建）都把 ``lark_listver:{table}``
# +1，旧键自然不再命中，不用逐个删。飞书里被别人改的内容最多滞后一个 TTL。
_LIST_CACHE_TTL = 900
_LIST_VERSION_TTL = 7 * 24 * 3600
_LIST_CACHE_VERSION = 1

# 批量写入的单次条数上限（飞书 batch_update 上限 500）。
_BATCH_SIZE = 200

# 飞书时区约定：多维表格时间按北京时间展示/解析。
_BEIJING = timezone(timedelta(hours=8))

# 列表关键字命中的字段已改为**按工作台配置**（``WorkbenchConfig.search_fields``）：
# 跨 base 时字段名完全不同，共用一份常量会把整单查询打进 400。


def _require_credentials() -> tuple[str, str, str]:
    """校验**飞书应用**配置（app_id / app_secret），返回 ``(app_id, app_secret, base_url)``。

    只校验应用凭证，不校验目标表格：本模块现在服务**多个 base**（富士康流程TSET、威达等），
    具体用哪张表由每个工作台配置自带的 app_token/table_id 决定，见
    ``FeishuBitableService._require_datasource``。

    异常:
    - CustomException: 应用凭证缺失时抛出。
    """
    if not settings.LARK_APP_ID or not settings.LARK_APP_SECRET:
        raise CustomException(msg="飞书多维表格未配置（LARK_APP_ID / LARK_APP_SECRET 为空）")
    return settings.LARK_APP_ID, settings.LARK_APP_SECRET, settings.LARK_BASE_URL


def _open_api(path: str) -> str:
    """飞书开放平台地址（不限本表）。

    通讯录这类接口挂在 ``/contact`` 下而不是 ``/bitable/...``，既不需要 app_token
    也不需要 table_id，所以不能走 ``_endpoint``（那个会连带校验数据源）。
    """
    _app_id, _secret, base_url = _require_credentials()
    return f"{base_url}{path}"


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """返回模块级共享 httpx 客户端（连接复用，避免每次请求重建连接池 + TLS 握手）。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=settings.LARK_HTTP_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def _http_json(method: str, url: str, token: str, **kwargs: Any) -> dict:
    """发起带 Bearer 认证的请求并解析 JSON 响应。

    参数:
    - method (str): HTTP 方法。
    - url (str): 请求地址。
    - token (str): tenant_access_token。
    - kwargs: 透传给 httpx 的参数。

    返回:
    - dict: 响应 JSON。

    异常:
    - CustomException: 非 JSON 响应时抛出。
    """
    headers: dict[str, str] = {}
    if token:  # token 为空时（如换取 token 本身）不发送 Authorization
        headers["Authorization"] = f"Bearer {token}"
    headers.update(kwargs.pop("headers", {}) or {})
    client = _get_client()
    r = await client.request(method, url, headers=headers, **kwargs)
    r.raise_for_status()
    try:
        return r.json()
    except json.JSONDecodeError:
        log.error(f"飞书 API 非 JSON 响应: {r.text[:500]}")
        raise CustomException(msg="飞书接口返回异常") from None


def _check_lark_error(data: dict, action: str) -> None:
    """统一检查飞书业务错误码。

    参数:
    - data (dict): 飞书响应 JSON。
    - action (str): 动作描述（用于错误文案）。

    异常:
    - CustomException: code 非 0 时抛出（HTTP 400，属调用方/配置问题）。
    """
    code = data.get("code")
    if code is not None and code != 0:
        msg = data.get("msg") or "未知错误"
        raise CustomException(msg=f"飞书{action}失败: [{code}] {msg}", status_code=status.HTTP_400_BAD_REQUEST)


# 飞书「这个 tenant_access_token 不算数」的业务码。**只认这几个**：重换一个 token 只对
# 这类错有用，把别的 400（参数错、字段不存在）也当成 token 问题去重试，只会把一次失败
# 变成两次，还掩盖真正的报错。
_TOKEN_ERROR_CODES: frozenset[int] = frozenset(
    {
        99991661,  # Missing access token（下载附件不带 token 就是这个）
        99991663,  # Invalid access token for authorization（缓存里的 token 已失效）
        99991664,  # Invalid tenant access token
        99991665,  # App 无权访问该资源
    }
)


def _lark_code(payload: Any) -> int | None:
    """从响应体里抠出飞书业务码（没有就返回 None）。"""
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    return code if isinstance(code, int) else None


def _is_token_error_response(response: httpx.Response) -> bool:
    """这个失败响应是不是「token 不算数」。

    失效 token 在 HTTP 层是 400，而 body 里照样带业务码——所以判据是 **body 的 code**，
    不是状态码（400 也是「参数写错了」的报错码，认错了会去重试一个永远不对的请求）。
    """
    try:
        return _lark_code(response.json()) in _TOKEN_ERROR_CODES
    except ValueError:
        return False


def _is_token_error_payload(data: dict) -> bool:
    """响应体（HTTP 200）里带的是不是「token 不算数」的业务码。

    飞书有的接口对失效 token 回 200 + 非 0 的 code，只盯 HTTP 状态码会漏掉这一半。
    """
    return _lark_code(data) in _TOKEN_ERROR_CODES


def _lark_error_text(response: httpx.Response) -> str:
    """从失败的响应里尽量抠出可读的错误信息。

    附件的错误是**业务码 + JSON 体**（如 99991661 Missing access token），不是 HTTP 状态；
    直接抛状态码，排障时看不出到底哪一步不对。
    """
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "")[:200] or "无响应体"
    code = payload.get("code")
    msg = payload.get("msg") or payload.get("message") or "未知错误"
    return f"[{code}] {msg}" if code is not None else str(msg)


def _content_disposition(filename: str) -> str:
    """构造下载响应头。

    文件名几乎都是中文，而 HTTP 头只能写 latin-1 —— 直接把中文塞进 ``filename="..."``
    会让 Starlette 在编码响应头时抛 ``UnicodeEncodeError``（表现为下载接口 500）。
    故给 ASCII 兜底名 + RFC 5987 的 ``filename*=UTF-8''`` 百分号编码，现代浏览器取后者。
    """
    fallback = re.sub(r"[^\x20-\x7e]", "_", filename) or "file"
    fallback = fallback.replace("\\", "_").replace('"', "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


_DISPOSITION_STAR = re.compile(r"filename\*=UTF-8''([^;]+)", re.IGNORECASE)
_DISPOSITION_PLAIN = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)


def _filename_from_disposition(disposition: str) -> str:
    """从 ``Content-Disposition`` 取文件名，优先 RFC 5987 的 ``filename*``。

    取不到就返回空串，由调用方回退（前端本来就知道附件名，会随请求带上）。
    """
    if not disposition:
        return ""
    star = _DISPOSITION_STAR.search(disposition)
    if star:
        return unquote(star.group(1).strip())
    plain = _DISPOSITION_PLAIN.search(disposition)
    return plain.group(1).strip() if plain else ""


async def _get_tenant_access_token(redis: Redis) -> str:
    """获取飞书 tenant_access_token，优先从 Redis 缓存读取。

    参数:
    - redis (Redis): Redis 客户端。

    返回:
    - str: tenant_access_token。
    """
    rc = RedisCURD(redis)
    cache_key = RedisInitKeyConfig.LARK_TENANT_ACCESS_TOKEN.key
    cached = await rc.get(cache_key)
    if cached:
        return str(cached.decode("utf-8")) if isinstance(cached, bytes) else str(cached)

    app_id, app_secret, base_url = _require_credentials()
    data = await _http_json(
        "POST",
        f"{base_url}/auth/v3/tenant_access_token/internal",
        token="",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    token = data.get("tenant_access_token")
    if not token:
        raise CustomException(
            msg=f"获取飞书 tenant_access_token 失败: [{data.get('code')}] {data.get('msg') or '未知错误'}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await rc.set(key=cache_key, value=str(token), expire=settings.LARK_TOKEN_CACHE_TTL)
    log.info(f"飞书 tenant_access_token 已缓存，TTL={settings.LARK_TOKEN_CACHE_TTL}s")
    return str(token)


def _related_page(role: str, field_name: str) -> RelatedTable | None:
    """``field_name`` 是不是这个工作台配的**反查页** key（如 ``iFreight``）。

    反查页在本表上**没有**同名关联字段（那条边是反的），所以凡是拿字段名去问「你指向哪张表」
    的地方（`_link_target_table`）都会扑空——这些地方得先来这里认一下，表 id 直接从配置取。
    """
    return next((rel for rel in get_workbench(role).related if rel.key == field_name), None)


async def _drop_tenant_access_token(redis: Redis) -> None:
    """删掉缓存的 tenant_access_token，逼下次调用重新换一个（见 `_send`）。

    缓存键只有一个、不带 app 维度（见 `RedisInitKeyConfig.LARK_TENANT_ACCESS_TOKEN`），
    所以两个 base 共用同一个 token，清一次两边一起换。
    """
    await RedisCURD(redis).delete(RedisInitKeyConfig.LARK_TENANT_ACCESS_TOKEN.key)


# ============================ 字段值归一化 ============================


def _to_number(value: Any) -> Any:
    """数字字段值 → int/float（飞书 Number 字段可能返回字符串）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.replace(",", "").strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return value
    return value


def _join_text(value: Any) -> str | None:
    """把 ``[{"text":..}]`` / ``{"text":..}`` / ``str`` 抽成纯字符串。

    表内文本存在全角空格占位（如 ``"\\u3000"``）与首尾空白，统一 strip 后为空视为无值，
    否则前端会把占位符当成真实内容显示。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                t = item.get("text")
                if t:
                    parts.append(str(t))
            elif item is not None:
                parts.append(str(item))
        joined = " ".join(parts).strip()
        return joined or None
    if isinstance(value, dict):
        # 公式/查找字段返回的不是文本片段，而是**带结果类型信息的包装**：
        # ``{"type": 1, "value": [{"text": "海信模具//"}]}``——公式结果是数字/选项/日期时
        # 里的 ``type`` 跟着变。这个形状没有顶层 ``text``，前面的分支全都认不出，
        # 一路落到最后返回 None，表现是「飞书里明明有值、系统里整列空白」。
        if "value" in value:
            return _join_text(value["value"])
        t = value.get("text")
        return str(t).strip() or None if t else None
    text = str(value).strip()
    return text or None


def _unwrap_lookup_value(value: Any) -> Any:
    """查找(19)/公式(20) 的返回值 → 前端简单值。

    这两个类型的返回值外面包了一层 ``{"type": <源字段类型>, "value": [...]}``，那层 ``type``
    是**唯一**能说明「里面这串数字是日期还是数字」的线索。只把 ``value`` 抽成文本
    （``_join_text`` 原本做的事）会把它丢掉：实测对客方案的「最早航班时间」是查找列，
    壳里 ``type=5``、``value=[1789187580000]``，丢壳之后界面上就是 ``1789187580000``。

    所以**日期**按壳里的类型再归一化一次，让查找列与源字段长得一样。其余类型仍走
    ``_join_text``——文本/选项的抽取本来就是对的，改成别的形态反而会动到已有行为
    （比如把单项选择的查找列从 ``"AAK"`` 变成 ``["AAK"]``，过滤与排序都要跟着改）。
    """
    if isinstance(value, dict) and value.get("type") == DATE_TYPE and "value" in value:
        raw = value["value"]
        # 即使源字段是单值，这里也是**数组**；多值查找就是多个元素。
        # 只有单元素时才敢当时间戳解析，多值的退回文本拼接。
        single = raw[0] if isinstance(raw, list) and len(raw) == 1 else raw
        iso = _ts_to_iso(single)
        if iso is not None:
            return iso
    return _join_text(value)


def _ts_to_iso(value: Any) -> str | None:
    """毫秒时间戳 → 北京时间 ISO 字符串。"""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def _iso_to_ts(value: Any) -> int | None:
    """北京时间 ISO 字符串 → 毫秒时间戳。"""
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise CustomException(msg=f"日期格式无法解析: {value}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BEIJING)
    return int(dt.timestamp() * 1000)


def _users_to_names(value: Any) -> list[str] | None:
    """人员字段 ``[{id,name,...}]`` → 姓名列表。"""
    if value is None:
        return None
    if isinstance(value, list):
        names = [str(u.get("name") or u.get("id")) for u in value if isinstance(u, dict)]
        return names or None
    return None


def _users_to_refs(value: Any) -> list[dict[str, str]]:
    """人员字段 ``[{id, name, ...}]`` → ``[{id, name}]``。

    与 ``_users_to_names`` 取的是**同一份**原始列表，只是一个留姓名给人看、
    一个留 open_id 给改的时候用——改人必须知道改的是哪个 id，姓名重名时更没法猜。
    """
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict) and item.get("id"):
            refs.append({"id": str(item["id"]), "name": str(item.get("name") or item["id"])})
    return refs


def _metas_from_raw(raw_items: list[dict[str, Any]]) -> list[FieldMetaSchema]:
    """原始字段定义 → 字段元数据（**保持传入顺序**，即飞书表里的字段顺序）。

    做成模块级函数而不是实例方法：除了本工作台的表，**关联表**（如商机详情里的航线报价表）
    也要按同一套规则转元数据，而它不属于任何工作台，``get_fields()`` 只认配置里的那张表。

    「类型 → 可编辑/选项/多选」的规则只能有这一份，否则本表与关联表会各走一套。
    """
    metas: list[FieldMetaSchema] = []
    for item in raw_items:
        ft = int(item.get("type", 0))
        prop = item.get("property") or {}
        metas.append(
            FieldMetaSchema(
                name=str(item.get("field_name") or ""),
                type=ft,
                ui_type=str(item.get("ui_type") or ""),
                read_only=ft in READONLY_TYPES,
                editable=ft in EDITABLE_TYPES,
                # 只有单选/多选带可选项（本表另有 8 个 lookup 列不在此列）
                options=[str(o["name"]) for o in prop.get("options", [])] if ft in (3, 4) else [],
                # 只有人员字段的 property 里带 multiple（本表「负责航线」true、
                # 「商机负责人」false），别的类型一律当多值处理。
                multiple=bool(prop["multiple"]) if "multiple" in prop else True,
                # 飞书里给字段写的说明，详情面板显示在字段名下面（本表不少字段有，
                # 如「商机信息」写着它由哪几段拼出来）。以前整个丢掉了。
                description=str(item.get("description") or ""),
                # 关联字段指向哪张表（其余类型的 property 里没有 table_id，给空串）
                link_table_id=str(prop.get("table_id") or "") if ft in LINK_TYPES else "",
                # 主字段：关联字段要显示「对方叫什么」时，取的就是目标表这一列
                is_primary=bool(item.get("is_primary")),
            )
        )
    return metas


def _filter_users(
    users: list[dict[str, str]], search: str | None, hidden: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """按姓名在**本地**过滤通讯录（与关联字段候选同一口径）。

    整租户只十几个人，一次拉回来当场筛比每次输入都发一个请求简单也快得多。

    ``hidden`` 是**按姓名精确匹配**的排除名单（离职、不参与本业务的人）。放在这里而不是
    缓存层：通讯录的 Redis 缓存是租户级的（两个 base 共用），把某张表的人员偏好写进去会
    污染另一个 base。
    """
    visible = [u for u in users if u["name"] not in hidden]
    keyword = (search or "").strip().lower()
    if not keyword:
        return visible
    return [u for u in visible if keyword in u["name"].lower()]


def _links_to_text(value: Any) -> list[str] | None:
    """关联字段 ``[{record_ids, text, ...}]`` → 关联记录显示文本列表。"""
    if value is None:
        return None
    # 单条关联的形态也可能是**裸 dict**（``{"link_record_ids": [...]}``），
    # 见 ``_links_to_ids`` 的说明。
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        out = [str(item["text"]) for item in value if isinstance(item, dict) and item.get("text")]
        return out or None
    return None


def _links_to_ids(value: Any) -> list[str]:
    """关联字段 → 关联记录的 record_id 列表。

    飞书在不同接口/版本上返回的关联结构不一致，逐个兜底，避免某个形态漏掉导致合并组算错：

    - ``[{"link_record_ids": [id, ...]}]`` —— ``records/search`` 投影关联字段时的常见形态
    - ``{"link_record_ids": [id, ...]}`` —— **只有一个关联值时飞书直接返回裸 dict，
      不再套一层数组**；只认列表会让这类记录的合并关联被静默丢光（合并组算成 0 个）
    - ``link_record_ids`` / ``record_ids`` / ``record_id`` / ``id`` / ``value`` 各种键名
    - 纯字符串 id 或 id 数组
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        # 裸 dict 按单元素列表处理，键名兜底逻辑与下面完全一致
        value = [value]
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item:
                ids.append(item)
            continue
        if not isinstance(item, dict):
            continue
        got = False
        for key in ("link_record_ids", "record_ids"):
            raw = item.get(key)
            if isinstance(raw, list) and raw:
                # 递归：列表里可能还是 dict（``[{"record_id": id}]``），直接 str() 会得到
                # "{'record_id': ...}" 这种垃圾 id
                ids.extend(_links_to_ids(raw))
                got = True
                break
        if got:
            continue
        for key in ("record_id", "id", "value"):
            raw = item.get(key)
            if isinstance(raw, str) and raw:
                ids.append(raw)
                break
    # 去重保序
    return list(dict.fromkeys(ids))


def _value_in(value: Any, wanted: frozenset[str]) -> bool:
    """归一化后的字段值是否命中候选集合（多选字段**命中任一项**即可）。

    单选/文本归一化出来是字符串、多选是列表，两种情况都要认：客商表的「客商角色」是多选
    （一家客商可以既是客户又是订舱代理），按字符串比会把整列都漏掉。
    """
    values = value if isinstance(value, list) else [value]
    return any(str(item) in wanted for item in values)


def _points_back_at(record: dict[str, Any], field: str, wanted: set[str]) -> bool:
    """这条**从表**记录是不是指着 ``wanted`` 里的某条本表记录。

    反查页取数靠这个（见 `linked_records`）：边是反的，本表记录上没有任何线索，只能反过来看
    从表那条记录的 ``field`` 列里有没有它的 id。

    ``link_ids`` 里是**原始**的飞书 id，形态与 `_value_in` 一样不保证是列表（单值关联可能给
    单个值），所以先裹一层——直接拿字符串去迭代等于按**字符**比，``wanted={"o"}`` 会把
    ``"o1"`` 也认成命中。两边再各过一遍 ``str`` 才比，免得 123 与 "123" 失配。

    ``field`` 不在 ``link_ids`` 里（那条记录没挂任何订单）时给空，不能因此报错——表里本来
    就有孤儿行。

    写成模块级纯函数是为了能单测：少认一种形态、少了 id 集合的积，都不会报错，只会让某一页
    少几条记录，界面上看不出来。
    """
    ids = (record.get("link_ids") or {}).get(field) or []
    values = ids if isinstance(ids, list) else [ids]
    return bool(wanted.intersection({str(rid) for rid in values}))


def _project_record(record: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """把归一化记录裁到 ``names`` 这几个字段上。

    同一个工作台的多个反查页常常是**同一张从表**（国内操作三页都指向 iFreight，只差铺哪几列），
    一次扫描的要是「各页字段的并集」才划算——但每一页只能看到自己点名的列，多铺的列会
    在界面上凭空长出来。所以扫描用并集、下发前按页裁一刀。

    三份平行映射（``fields`` / ``link_ids`` / ``user_refs``）都要一起裁：只裁 ``fields``
    的话，被裁掉的那些字段的 id 还留在 ``link_ids`` 里，前端按它去取数会取到这一页根本
    没显示的东西。
    """
    return {
        "record_id": record.get("record_id") or "",
        "fields": {k: v for k, v in (record.get("fields") or {}).items() if k in names},
        "link_ids": {k: v for k, v in (record.get("link_ids") or {}).items() if k in names},
        "user_refs": {k: v for k, v in (record.get("user_refs") or {}).items() if k in names},
    }


def _single_status(
    quote_has: bool,
    got: str,
    note: str,
    priced: bool,
    handover: bool,
) -> str:
    """客服的「单票状态」：由商机与它那张订单上的几个字段推导，飞书侧不存这个枚举。

    判定**从后往前**排：先问「走完了没」，再问下一步该谁干。业务给的五条规则本来就是这个
    顺序，写成 if 链时把终态放前面才不会被后面的条件覆盖：

    1. 订单的「交接给国内操作」勾了 → **已完成**（最后一步，也是唯一的人工收尾动作）；
    2. 没拿到货且「未拿到货备注」填了 → **已完成**（这单到此为止，业务确认「已完成优先」：
       即使航线那边已核价，也该显示已完成，不该退回「待交接操作」）；
    3. 订单的「负责航线已核价」= 已核价 → **待交接操作**；
    4. 「是否拿到货」= 是 → **待核价**；
    5. 商机上有航线报价 → **待确认拿到货**；
    6. 都不是 → **待航线报价**。

    「否 + 备注为空」故意不命中所任何一条，落在 5/6：货没拿到又没说清原因的单子，
    停在「待确认拿到货」是提醒人补备注，而不是当它已经结束。
    """
    if handover:
        return "已完成"
    if got == "否" and (note or "").strip():
        return "已完成"
    if priced:
        return "待交接操作"
    if got == "是":
        return "待核价"
    if quote_has:
        return "待确认拿到货"
    return "待航线报价"


SINGLE_STATUS_VALUES: tuple[str, ...] = (
    "待航线报价",
    "待确认拿到货",
    "待核价",
    "待交接操作",
    "已完成",
)


def _rebuild_updates(
    groups: dict[str, set[str]],
    link_field: str,
    removed: set[str],
    *,
    clear_removed: bool,
) -> list[dict[str, Any]]:
    """把每个合并组重写为「剔除 ``removed`` 之后的成员」。

    剩余 ≥2 票写剩余成员（组继续存在）；只剩 1 票写空数组（一票不成组）；
    ``clear_removed`` 为真时被剔除的票也写空。孤票（一元组）本身没有组可维护，
    但调用方明确要求清空时仍会写空（幂等，且与旧实现的语义一致）。

    合并/拆出共用这一段「组内成员重算」的逻辑：跨组重组必须先摘旧组再写新组，
    只写新组会让旧组剩下的票继续引用被摘走的票（§4.8 场景3/4）。
    """
    updates: list[dict[str, Any]] = []
    for members in groups.values():
        rest = members - removed
        if len(rest) > 1:
            updates.extend(
                {"record_id": rid, "fields": {link_field: sorted(rest)}} for rid in sorted(rest)
            )
        elif len(rest) == 1:
            updates.append({"record_id": next(iter(rest)), "fields": {link_field: []}})
        if clear_removed:
            updates.extend(
                {"record_id": rid, "fields": {link_field: []}} for rid in sorted(members & removed)
            )
    return updates


def normalize_field_value(field_type: int, value: Any) -> Any:
    """飞书原生值 → 前端简单类型。"""
    if value is None:
        return None
    if field_type == 17:  # 附件
        # 只留前端真正要用的字段：飞书原始对象里还带 ``url`` / ``tmp_url``，
        # 那两个 URL 里含临时访问凭证，没必要送到浏览器（§10.3 不显示原始 URL）。
        if not isinstance(value, list):
            return value
        return [
            {
                "file_token": item.get("file_token"),
                "name": item.get("name"),
                "size": item.get("size"),
                "type": item.get("type"),
            }
            for item in value
            if isinstance(item, dict)
        ]
    if field_type == 1:  # 文本
        return _join_text(value)
    if field_type == 2:  # 数字
        return _to_number(value)
    if field_type == 3:  # 单选
        return value
    if field_type == 4:  # 多选
        return value
    if field_type == 5:  # 日期
        return _ts_to_iso(value)
    if field_type == 7:  # 复选框
        return bool(value)
    if field_type == 11:  # 人员
        return _users_to_names(value)
    if field_type == 13:  # 电话
        return value
    if field_type == 15:  # 超链接
        return value.get("text") or value.get("link") if isinstance(value, dict) else value
    if field_type in LINK_TYPES:  # 单向/双向关联
        return _links_to_text(value)
    if field_type in (19, 20):  # 查找/公式
        return _unwrap_lookup_value(value)
    return value  # 自动编号(1005)等其余类型透传


def _form_control(field_type: int) -> str:
    """字段类型 → 新建表单该挂什么控件（``readonly`` 表示本系统填不了）。

    **不要用 ``EDITABLE_TYPES`` 来判断**：那是索引页内联编辑（改完即写）支持的子集，
    与「新建表单能挂什么控件」是两回事。关联字段（18/21）只收了单向关联、人员（11）
    压根没收，照它判断会把「客户」和「负责航线」都当成只读——而「客户」是表单里第一个
    必填项，标成只读等于这个表单永远建不出记录。
    """
    if field_type in LINK_TYPES:
        return "link"
    if field_type == USER_TYPE:
        return "user"
    return {
        1: "text",
        13: "text",
        2: "number",
        3: "single",
        4: "multi",
        5: "date",
        7: "checkbox",
        17: "attachment",
    }.get(field_type, "readonly")


def denormalize_field_value(field_type: int, value: Any) -> Any:
    """前端简单类型 → 飞书写入格式。"""
    if value is None:
        return None
    if field_type == 1:  # 文本
        return str(value)
    if field_type == 2:  # 数字
        return _to_number(value)
    if field_type == 3:  # 单选
        return str(value)
    if field_type == 4:  # 多选
        return list(value) if isinstance(value, list) else [str(value)]
    if field_type == 5:  # 日期
        return _iso_to_ts(value)
    if field_type == 7:  # 复选框
        return bool(value)
    if field_type == 13:  # 电话
        return str(value)
    if field_type == USER_TYPE:  # 人员：写入 [{"id": open_id}]
        # 飞书只认 id，送姓名会被拒；这里接受 open_id 字符串（单选）或字符串数组（多选）。
        # 人员字段的值**永远是数组**，哪怕字段本身是单选——单个也包一层。
        items = [value] if isinstance(value, str) else (list(value) if isinstance(value, list) else [])
        out: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out.append({"id": str(item["id"])})
            elif isinstance(item, str) and item:
                out.append({"id": item})
        return out
    if field_type == 15:  # 超链接
        return {"text": str(value), "link": str(value)}
    if field_type == 17:  # 附件：写入 [{"file_token": ...}]，其余字段（url 等）必须去掉
        if not isinstance(value, list):
            return []
        out: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict) and item.get("file_token"):
                out.append({"file_token": str(item["file_token"])})
            elif isinstance(item, str) and item:
                out.append({"file_token": item})
        return out
    if field_type in LINK_TYPES:  # 关联：写入 record_id 字符串数组
        if isinstance(value, str):
            return [value] if value else []
        return [str(x) for x in value] if isinstance(value, list) else []
    return value  # 其余可写类型本期不透传


# ============================ 过滤条件构造 ============================


def _is_false_condition(field_name: str) -> dict[str, Any]:
    """勾选框「未勾选」条件。

    ``value`` 必须是**数组**；传裸 ``False`` 或字符串都会触发
    ``field validation failed``。未填过值的勾选框同样命中 ``is false``。
    """
    return {"field_name": field_name, "operator": "is", "value": [False]}


def _contains_condition(field_name: str, keyword: str) -> dict[str, Any]:
    """文本字段包含条件（``value`` 同样必须是数组）。"""
    return {"field_name": field_name, "operator": "contains", "value": [keyword]}


def _is_true_condition(field_name: str) -> dict[str, Any]:
    """勾选框「已勾选」条件（``value`` 必须是数组）。"""
    return {"field_name": field_name, "operator": "is", "value": [True]}


def _is_condition(field_name: str, value: str) -> dict[str, Any]:
    """字段精确等于条件（``value`` 必须是数组）。"""
    return {"field_name": field_name, "operator": "is", "value": [value]}


def _build_filter(
    cfg: WorkbenchConfig,
    pending_field: str | None,
    keyword: str,
    field_types: dict[str, int],
) -> dict[str, Any] | None:
    """构造 ``records/search`` 的 filter。

    「待办」按**单个**勾选框筛选（如「待订舱」= 订舱单未勾选），而不是多个勾选框求并集：
    本表多数勾选框是完成标记，未勾选的行接近全表，取并集会让「待办」等于「全部」而失去意义
    （实测客服两个勾选框并集 1958/1990）。多个待办项以分项计数卡片呈现，点击即筛该项。

    「关键字」是多个文本字段的 **OR**，与待办条件之间是 **AND**，因此需要嵌套。飞书不接受
    把子过滤器直接塞进 ``conditions``（会 ``field validation failed``），必须放在 ``children``，
    故此处按需在扁平与嵌套两种形态间切换。

    ``field_types`` 用来**剔除搜不了的列**：``contains`` 并非所有类型都支持，而飞书对一个坏
    条件会**整单拒掉**（``1254018 InvalidFilter``），一个字段配错就等于这个工作台的关键字
    搜索彻底不能用——症状还是「搜什么都报错」，很难看出是配置里哪一列的问题。故按类型过一遍，
    不支持的直接跳过并记日志。
    """
    parts: list[dict[str, Any]] = []
    if pending_field:
        if pending_field not in cfg.todo_checkboxes:
            raise CustomException(msg=f"工作台 {cfg.name} 的待办项不含「{pending_field}」")
        parts.append({"conjunction": "and", "conditions": [_is_false_condition(pending_field)]})
    if keyword:
        if not cfg.search_fields:
            raise CustomException(msg=f"工作台 {cfg.name} 未配置搜索字段，无法关键字搜索")
        usable = [f for f in cfg.search_fields if field_types.get(f) in _SEARCHABLE_TYPES]
        skipped = [f for f in cfg.search_fields if f not in usable]
        if skipped:
            log.warning(f"工作台 {cfg.name} 的搜索字段不支持关键字包含（已跳过）: {skipped}")
        if not usable:
            raise CustomException(msg=f"工作台 {cfg.name} 没有可用于关键字搜索的文本字段")
        parts.append(
            {
                "conjunction": "or",
                "conditions": [_contains_condition(f, keyword) for f in usable],
            }
        )
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"conjunction": "and", "children": parts}


def _combine_filter(*parts: dict[str, Any] | None) -> dict[str, Any] | None:
    """把若干条件 AND 成一个**合法的顶层 filter**（全为空则返回 ``None``）。

    两个形态约束都是飞书实测出来的（``code 99992402 field validation failed``）：

    - **顶层不能是裸条件**，``filter.conjunction is required``。所以哪怕只有一个条件也得包一层
      ``{conjunction, conditions}``——这也是 `_build_filter` 单条件时返回 ``parts[0]`` 而不是
      裸条件的原因。踩过：行级权限只有一个 owner 条件时直接裸传，整个列表 400。
    - **子过滤器不能塞进 ``conditions``**，必须放 ``children``；且 ``children`` 里的每一项
      **本身也得是** ``{conjunction, conditions}`` 形态，裸条件放进去同样 400。

    所以这里把每一项都归一成带 ``conjunction`` 的形态，再决定是摊平还是嵌套。
    """
    conds: list[dict[str, Any]] = []
    for part in parts:
        if part is None:
            continue
        conds.append(part if "conjunction" in part else {"conjunction": "and", "conditions": [part]})
    if not conds:
        return None
    if len(conds) == 1:
        return conds[0]
    return {"conjunction": "and", "children": conds}


def _build_sort(
    cfg: WorkbenchConfig, fields_by_name: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """构造 ``records/search`` 的 sort。

    排序必须在服务端做：飞书只给 ``page_token`` 游标、没有 offset，本地排序只能排当前一页，
    规则落不到全部记录上。字段不存在时跳过（见 ``list_workbench`` 的说明），
    全部缺失则返回 ``None``，退回飞书默认顺序而不是报错。
    """
    spec = [
        {"field_name": name, "desc": desc}
        for name, desc in cfg.sort
        if name in fields_by_name
    ]
    missing = {name for name, _ in cfg.sort} - {s["field_name"] for s in spec}
    if missing:
        log.warning(f"工作台 {cfg.key} 的排序字段在飞书表中不存在（已跳过）: {sorted(missing)}")
    return spec or None


# ============================ 业务操作 ============================


class FeishuBitableService:
    """飞书多维表格读写服务。

    **数据源（app_token + table_id）是实例状态，不再只认全局 settings**：
    本模块现在同时服务多个 base（富士康流程TSET、威达等），每个工作台配置自带
    ``app_token``/``table_id``。传 ``role`` 时用该工作台的表格，不传则回落到
    settings（老行为，富士康那套就是靠这个工作）。
    """

    def __init__(self, redis: Redis, role: str = "") -> None:
        self._redis = redis
        cfg = get_workbench(role) if role else None
        # 留一份配置：行级数据权限（`_owner_filter`）要读 `owner_field`，而
        # `count_total()` 这类不带 role 的方法也得拿到它
        self._cfg = cfg
        # 配置里留空 = 用全局 settings，兼容既有工作台
        self._app_token = (cfg.app_token if cfg else "") or settings.LARK_APP_TOKEN
        self._table_id = (cfg.table_id if cfg else "") or settings.LARK_TABLE_ID

    async def _token(self) -> str:
        return await _get_tenant_access_token(self._redis)

    async def _send(self, method: str, url: str, **kwargs: Any) -> dict:
        """带 **token 自愈**的 ``_http_json``：token 不算数就清缓存、重换、原样再打一次。

        飞书的 tenant_access_token 换成后要缓存 ``LARK_TOKEN_CACHE_TTL``（最长 100 分钟），
        但缓存里那个可能提前作废（应用被改动授权、飞书侧轮换等）。此时**所有**接口一起挂：
        HTTP 层是 400（body 里是 ``99991663``），业务层是 200 + 同样的 code——两种看着都
        像「参数写错了」，照着参数去查会查很久（实测踩过两次，见备忘）。

        重试**只做一次**，且只在认得出是 token 类报错时：第二遍还失败就原样冒泡，因为那说明
        换新 token 也救不了（密钥错、应用被停用），再重试只是把一次失败拖成三次。

        第二遍用的是新 token，所以调用方拿到的 `token` 变量在这之后是**旧**的——本模块所有
        调用点都改成现取（不再把 token 传来传去），就是为了不留这个坑。
        """
        token = await self._token()
        try:
            data = await _http_json(method, url, token, **kwargs)
        except httpx.HTTPStatusError as exc:
            if not _is_token_error_response(exc.response):
                raise
        else:
            if not _is_token_error_payload(data):
                return data
        log.warning(f"飞书 access token 已失效（{method} {url}），清缓存重换后重试一次")
        await _drop_tenant_access_token(self._redis)
        return await _http_json(method, url, await self._token(), **kwargs)

    def _require_datasource(self) -> None:
        """校验本实例的目标表格已配置（app_token / table_id 都由实例携带）。"""
        if not self._app_token or not self._table_id:
            raise CustomException(msg="飞书多维表格未配置（app_token / table_id 为空）")

    async def _endpoint(self, path: str) -> str:
        _app_id, _secret, base_url = _require_credentials()
        self._require_datasource()
        return f"{base_url}/bitable/v1/apps/{self._app_token}/tables/{self._table_id}{path}"

    def _fields_cache_key(self, table_id: str = "") -> str:
        """字段元数据缓存键**必须带数据源**。

        否则两个 base 会共用 ``lark_fields:<app>:<table>`` —— 若两表恰好同名就会
        互相污染（拿到别的表的字段定义），排查起来毫无头绪。

        还带一个**版本号**：缓存里存的是 ``FieldMetaSchema`` 的序列化结果，给这个结构
        加字段（如 ``description``）后，老缓存反序列化出来只会得到默认空值，且要等
        ``_FIELDS_CACHE_TTL`` 才自愈——表现为「代码改了，界面十分钟内没变化」。
        改了 ``FieldMetaSchema`` 就把版本号 +1，让老缓存直接作废。

        ``table_id`` 非空即缓存**另一张表**的字段定义（详情页展开关联分区时用）。
        """
        return f"lark_fields:v{_FIELDS_CACHE_VERSION}:{self._app_token}:{table_id or self._table_id}"

    async def _table_endpoint(self, table_id: str, path: str) -> str:
        """同一个 base 里**另一张表**的接口地址。

        关联字段的候选记录、目标表的主字段都要去别的表取，而 ``_endpoint`` 只会拼
        本工作台的表。只换表不换 base：跨 base 的关联本来就取不到。
        """
        _app_id, _secret, base_url = _require_credentials()
        if not self._app_token or not table_id:
            raise CustomException(msg="飞书多维表格未配置（app_token / table_id 为空）")
        return f"{base_url}/bitable/v1/apps/{self._app_token}/tables/{table_id}{path}"

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        """拉取**原始**字段定义（带 ``property``），不传 ``table_id`` 即本工作台的表。

        与 ``_load_fields`` 的区别是这里保留 ``property`` 与 ``field_id``：
        建表单要知道每个字段指向哪张关联表，而 ``FieldMetaSchema`` 把这两样都丢了。

        必须翻页：飞书 ``/fields`` 单页上限 100，本表有 178 个字段，只取第一页会静默
        丢掉后 78 个（表现为「配置里的字段在表中不存在」），故这里循环到 ``has_more`` 为假。
        """
        url = await (self._table_endpoint(table_id, "/fields") if table_id else self._endpoint("/fields"))
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = await self._send("GET", url, params=params)
            _check_lark_error(data, "查询字段")
            body = data.get("data") or {}
            items.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = str(body.get("page_token") or "") or None
            if not page_token:
                break
        return items

    async def _load_fields(self) -> list[FieldMetaSchema]:
        """从飞书拉取字段元数据（未走缓存）。"""
        return _metas_from_raw(await self._raw_field_items())

    async def _table_fields(self, table_id: str) -> list[FieldMetaSchema]:
        """**另一张表**的字段元数据（Redis 缓存，与工作台表同一套机制、同一个键格式）。

        详情页展开关联分区时要拿目标表的字段定义排版。每次开抽屉都去拉一遍 ``/fields``
        要一秒多（178 个字段的表要翻两页），而字段结构一分钟内不会变，缓存掉。
        """
        if table_id == self._table_id:
            return await self.get_fields()
        rc = RedisCURD(self._redis)
        key = self._fields_cache_key(table_id)
        cached = await rc.get(key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8")) if isinstance(cached, bytes) else json.loads(str(cached))
                return [FieldMetaSchema(**m) for m in raw]
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书字段缓存损坏，忽略并重新拉取")

        metas = _metas_from_raw(await self._raw_field_items(table_id))
        await rc.set(key=key, value=json.dumps([m.model_dump() for m in metas]), expire=_FIELDS_CACHE_TTL)
        return metas

    async def _link_target_table(self, field_name: str, table_id: str = "") -> str:
        """本表某个关联字段**指向的目标表 id**。

        关联字段的值在飞书里只是一个 record_id：对方叫什么名字、有哪些字段，都得拿这个表 id
        去另一张表查，记录本身不带表名/表 id。所以每处「展开关联」都要先回本表的字段定义里翻一遍。

        ``table_id`` 非空即去**别的表**上找这个字段（留空 = 本工作台的表）。这是给**二级关联**
        用的：对客方案上的「费用明细」长在对客方案表上，而不是工作台表（商机主表）上，
        只扫工作台表会得到「飞书表中不存在字段「费用明细」」。父表是谁由页面决定，
        和 ``create_linked_record`` 让调用方自己给回指字段是同一个道理。

        **走缓存过的字段元数据**（`get_fields` / `_table_fields`），不再用 `_raw_field_items`：
        后者每次实时翻 `/fields`（178 字段翻两页，实测 0.65~0.76 秒），而本方法**每点一次关联
        分区就会调一次**，白等的是整整三分之一个请求的时间。二次关联要用到的 ``property.table_id``
        在 `FieldMetaSchema.link_table_id` 里已经有一份（见 `_metas_from_raw`），不必回原始定义里翻。
        """
        metas = await (self._table_fields(table_id) if table_id else self.get_fields())
        meta = next((m for m in metas if m.name == field_name), None)
        if meta is None:
            raise CustomException(msg=f"飞书表中不存在字段「{field_name}」")
        if meta.type not in LINK_TYPES:
            raise CustomException(msg=f"字段「{field_name}」不是关联字段，没有关联记录")
        if not meta.link_table_id:
            raise CustomException(msg=f"关联字段「{field_name}」没有指向任何表")
        return meta.link_table_id

    async def _field_types_of(self, table_id: str = "") -> dict[str, int]:
        """字段名 → 类型；``table_id`` 为空即本工作台的表。

        写关联表（详情页就地改报价）要用**那张表**的类型表去归一化，拿本表的会认不出字段。
        """
        if not table_id:
            return await self._field_types()
        return {m.name: m.type for m in await self._table_fields(table_id)}

    async def get_fields(self) -> list[FieldMetaSchema]:
        """获取字段元数据（Redis 缓存，供前端动态渲染）。"""
        rc = RedisCURD(self._redis)
        cached = await rc.get(self._fields_cache_key())
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8")) if isinstance(cached, bytes) else json.loads(str(cached))
                return [FieldMetaSchema(**m) for m in raw]
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书字段缓存损坏，忽略并重新拉取")

        metas = await self._load_fields()
        await rc.set(
            key=self._fields_cache_key(),
            value=json.dumps([m.model_dump() for m in metas]),
            expire=_FIELDS_CACHE_TTL,
        )
        return metas

    async def _field_types(self) -> dict[str, int]:
        """字段名 → 类型。"""
        return {m.name: m.type for m in await self.get_fields()}

    async def _invalidate_fields_cache(self) -> None:
        """清掉字段元数据缓存。

        表结构一变（建字段/改字段名）就必须清：`get_fields` 缓存 600s，不清的话新字段
        要过十分钟才出现在「自定义列」的可选字段里，表现为「刚建的列找不到了」。
        """
        await RedisCURD(self._redis).delete(self._fields_cache_key())

    async def _find_raw_field(self, field_name: str) -> dict[str, Any]:
        """按字段名拿飞书**原始**字段对象（含 ``field_id``）。

        ``FieldMetaSchema`` 是对外的裁剪结构，不带 ``field_id``，而改名接口要按 id 定位，
        所以这里单独扫一遍 ``/fields``（字段数少，且改名本就是低频操作）。
        """
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = await self._send("GET", await self._endpoint("/fields"), params=params)
            _check_lark_error(data, "查询字段")
            body = data.get("data") or {}
            for item in body.get("items") or []:
                if str(item.get("field_name") or "") == field_name:
                    return dict(item)
            if not body.get("has_more"):
                break
            page_token = str(body.get("page_token") or "") or None
            if not page_token:
                break
        raise CustomException(msg=f"飞书表中不存在字段「{field_name}」")

    async def create_text_field(self, name: str) -> dict[str, Any]:
        """在本工作台的表里新建一个**文本**字段——「空列」的载体。

        索引页的「空列」是要落库的自由录入列，飞书侧得先有字段才存得下值，故走这里建。
        注意这是**改生产表结构**：字段建出来后所有飞书用户都看得见，删要人工去飞书删。

        命名约定：调用方（前端空列）发过来的名字是 **``{显示名}_{用户名}_{短码}``**，
        显示名是用户在列配置里起的，后两段由前端拼好。这样即使表里本来就有个同名字段
        （比如「备注」），两人各建一个也不会撞——发送方因此**不做重名预检**，真撞上
        （同一用户同一短码，概率可忽略）由下面这道检查兜底并给出明确提示。
        本方法只管存下这个名字，不解析它的结构。

        参数:
        - name (str): 字段名（空列场景即 ``{显示名}_{用户名}_{短码}``）。

        返回:
        - dict: ``{"name": 实际字段名, "type": 字段类型}``。

        异常:
        - CustomException: 名字为空、与现有字段重名，或飞书拒绝（无建字段权限等）。
        """
        clean = name.strip()
        if not clean:
            raise CustomException(msg="列名不能为空")
        if any(m.name == clean for m in await self._load_fields()):
            raise CustomException(msg=f"表中已有字段「{clean}」，请换一个列名")

        data = await self._send(
            "POST",
            await self._endpoint("/fields"),
            json={"field_name": clean, "type": 1},
        )
        _check_lark_error(data, "新建字段")
        await self._invalidate_fields_cache()
        field = (data.get("data") or {}).get("field") or {}
        log.info(f"飞书新建字段成功: {clean}")
        return {"name": str(field.get("field_name") or clean), "type": int(field.get("type") or 1)}

    async def rename_field(self, field_name: str, new_name: str) -> dict[str, Any]:
        """把已有字段改名——「空列」改列名。

        只改 ``field_name``，不动字段里的数据；写的是同一门生产表结构操作，需人工确认。

        参数:
        - field_name (str): 原字段名。
        - new_name (str): 新字段名。

        返回:
        - dict: ``{"name": 新字段名, "type": 字段类型}``。

        异常:
        - CustomException: 新名为空、与现有字段重名，或飞书拒绝。
        """
        clean = new_name.strip()
        if not clean:
            raise CustomException(msg="列名不能为空")

        raw = await self._find_raw_field(field_name)
        field_type = int(raw.get("type") or 1)
        if clean == field_name:
            return {"name": clean, "type": field_type}
        others = {m.name for m in await self._load_fields()} - {field_name}
        if clean in others:
            raise CustomException(msg=f"表中已有字段「{clean}」，请换一个列名")

        field_id = str(raw.get("field_id") or "")
        if not field_id:
            raise CustomException(msg=f"字段「{field_name}」缺少 field_id，无法改名")
        data = await self._send(
            "PUT",
            await self._endpoint(f"/fields/{field_id}"),
            json={"field_name": clean},
        )
        _check_lark_error(data, "修改字段名")
        await self._invalidate_fields_cache()
        log.info(f"飞书字段改名成功: {field_name} -> {clean}")
        return {"name": clean, "type": field_type}

    def _normalize_record(
        self,
        item: dict[str, Any],
        fields_by_name: dict[str, int],
        link_fields: frozenset[str],
    ) -> dict[str, Any]:
        """飞书原始记录 → ``{record_id, fields, link_ids, user_refs}``。

        ``fields`` 里存的是给人看的简单值（人员存姓名、关联存显示文本），会写丢原始
        id；所以另有两份**平行**的原始标识：``link_ids``（合并组要用 record_id）、
        ``user_refs``（改人员要用 open_id）。按字段名对齐，只有非空时才出现。
        """
        raw_fields = item.get("fields") or {}
        normalized: dict[str, Any] = {}
        link_ids: dict[str, list[str]] = {}
        user_refs: dict[str, list[dict[str, str]]] = {}
        for name, raw in raw_fields.items():
            ft = fields_by_name.get(name)
            normalized[name] = normalize_field_value(ft, raw) if ft is not None else _join_text(raw)
            if name in link_fields:
                ids = _links_to_ids(raw)
                if ids:
                    link_ids[name] = ids
            elif ft == USER_TYPE:
                refs = _users_to_refs(raw)
                if refs:
                    user_refs[name] = refs
        return {
            "record_id": str(item.get("record_id") or ""),
            "fields": normalized,
            "link_ids": link_ids,
            "user_refs": user_refs,
        }

    async def _search_page(
        self,
        filter_obj: dict[str, Any] | None,
        page_token: str | None,
        page_size: int,
        field_names: list[str] | None = None,
        sort: list[dict[str, Any]] | None = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        """调一次 ``records/search``，返回原始 ``data``。

        **不接 token 参数**：token 由 ``_send`` 现取现用，失效时会自己换一个新的重试
        （调用方握手里的那个 token 在重试完之后就是旧的，传进来只会用错）。

        ``page_size`` 必须放在 **URL query**——放进 body 会被忽略并退回默认 500。
        ``sort`` 是 ``[{"field_name": ..., "desc": bool}]``，由飞书服务端排序。
        ``table_id`` 非空时查的是**另一张表**（关联表的主字段文本要按表去取）。
        """
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        body: dict[str, Any] = {}
        if field_names:
            body["field_names"] = field_names
        if sort:
            body["sort"] = sort
        if filter_obj:
            body["filter"] = filter_obj
        url = await (self._table_endpoint(table_id, "/records/search") if table_id else self._endpoint("/records/search"))
        data = await self._send("POST", url, params=params, json=body)
        _check_lark_error(data, "查询记录")
        return data.get("data") or {}

    async def get_workbench_config(self, role: str) -> WorkbenchConfigSchema:
        """工作台配置：列表列 + 待办判定 + 详情分组（字段元数据已解析）。

        分组里若出现表结构中不存在的字段（改表后配置未同步），降级为只读占位，
        不让整个配置接口失败——配置漂移不该阻断工作台打开。
        """
        cfg = get_workbench(role)
        metas = {m.name: m for m in await self.get_fields()}
        missing: set[str] = set()

        def resolve(names: tuple[str, ...]) -> list[FieldMetaSchema]:
            resolved: list[FieldMetaSchema] = []
            for name in names:
                meta = metas.get(name)
                if meta is None:
                    missing.add(name)
                    resolved.append(_UNKNOWN_FIELD.model_copy(update={"name": name}))
                else:
                    resolved.append(meta)
            return resolved

        base_fields = resolve(cfg.base_fields)
        groups = [
            WorkbenchGroupSchema(label=group.label, fields=resolve(group.fields))
            for group in cfg.groups
        ]
        # 详情 Tab 里点名的字段若已从表里删掉，直接丢弃（detail_fields 本来也不会带上它）
        missing.update(
            name
            for section in cfg.detail_sections
            for name in section.fields
            if name not in metas
        )
        if missing:
            log.warning(f"工作台 {role} 配置中的字段在飞书表中不存在（已降级为只读）: {sorted(missing)}")
        own_fields = [m for m in metas.values() if m.type != 3001]
        related_blocks = await self._related_blocks(cfg.related)
        return WorkbenchConfigSchema(
            key=cfg.key,
            name=cfg.name,
            description=cfg.description,
            columns=list(cfg.columns),
            cargo_fields=list(CARGO_FIELDS),
            todo_checkboxes=list(cfg.todo_checkboxes),
            search_fields=list(cfg.search_fields),
            merge_master_field=MERGE_MASTER_FIELD,
            merge_hawb_field=MERGE_HAWB_FIELD,
            sort=[WorkbenchSortSchema(field=name, desc=desc) for name, desc in cfg.sort],
            base_fields=base_fields,
            groups=groups,
            create_label=cfg.create_label,
            allow_group_sync=cfg.allow_group_sync,
            # 新建抽屉照飞书**表单视图**渲染（`get_create_form`），没配视图就是空抽屉：
            # 与其让「新建」点开一片空白，不如前端直接不显示它
            can_create=bool(cfg.create_form_view_id),
            # 全字段元数据：自定义列要能挑到分组之外的字段（不含按钮——它没有值）。
            # **只有本表的**：从表那几页的字段在 related_blocks 里单独下发，不混进这个池子
            # （混进来会让它们能被挑成索引页的列，而那些列是 1:N 的，摆不下）。
            fields=own_fields,
            # 详情页照飞书原样铺开的那一份：**48 个字段按飞书顺序 + 按钮**。
            # metas 由 _raw_field_items() 按飞书返回顺序插入，dict 保序，所以这里直接取
            # values() 就是表里的字段顺序，不需要另配一份顺序常量（那样一改表就漂移）。
            detail_fields=list(metas.values()),
            detail_groups=_detail_groups(list(metas.values()), cfg.detail_sections),
            # 详情抽屉里那几页从表（如国内操作的「iFreight 数据」）：独立于 detail_groups
            # ——它不是本表字段的一个划分，而是**另一张表**按反查字段捞回来的那几条
            related_blocks=related_blocks,
        )

    def _list_version_key(self) -> str:
        """列表缓存版本号键：写操作 +1，所有旧页缓存立刻不再命中。"""
        return f"lark_listver:v{_LIST_CACHE_VERSION}:{self._app_token}:{self._table_id}"

    async def _list_version(self) -> str:
        try:
            raw = await self._redis.get(self._list_version_key())
        except Exception as e:  # Redis 抖动不该拖垮列表
            log.warning(f"读取列表缓存版本失败，按 0 处理: {e!s}")
            return "0"
        if not raw:
            return "0"
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def _bump_list_version(self) -> None:
        """本系统写操作后调用：让列表页缓存整体失效。

        不是逐个删键（Redis 里按页、按搜索词有几十个键），而是版本号 +1——
        新请求拼出的键不再命中旧数据，旧键到期自然清理。
        """
        key = self._list_version_key()
        try:
            await self._redis.incr(key)
            await self._redis.expire(key, _LIST_VERSION_TTL)
        except Exception as e:
            log.warning(f"递增列表缓存版本失败（下次写操作会重试）: {e!s}")

    def _list_cache_key(
        self,
        role: str,
        filter_obj: dict[str, Any] | None,
        page_token: str | None,
        page_size: int,
        version: str,
    ) -> str:
        digest = hashlib.md5(
            json.dumps(filter_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return (
            f"lark_list:v{_LIST_CACHE_VERSION}:{self._app_token}:{self._table_id}:"
            f"{role}:{version}:{digest}:{page_token or '-'}:{page_size}"
        )

    async def list_workbench(
        self,
        role: str,
        pending: str | None = None,
        page_token: str | None = None,
        page_size: int = 50,
        search: str | None = None,
        current_user: Any | None = None,
        _prefetch: bool = True,
    ) -> dict[str, Any]:
        """工作台记录列表（服务端过滤 + 字段投影 + 游标分页）。

        ``pending`` 指定单个勾选框（须属于该工作台的待办项），只返回它未勾选的记录。
        ``current_user`` 有值时按 ``cfg.owner_field`` 做行级过滤（见 `_owner_filter`）。
        """
        cfg = get_workbench(role)
        # 走**缓存过的**字段元数据（`get_fields`），不是每次重新拉 /fields：这里除了类型
        # 还要拿关联字段指向的表 id，才能把「客户」「订单表」的 id 补成名字。
        metas = await self.get_fields()
        fields_by_name = {m.name: m.type for m in metas}
        link_targets = {m.name: m.link_table_id for m in metas if m.link_table_id}
        keyword = (search or "").strip()
        # 行级数据权限与关键字/待办条件是 AND 关系，不是二选一：少 AND 一层，
        # 客服一搜索就能搜出别人的商机。
        filter_obj = _combine_filter(
            _build_filter(cfg, pending, keyword, fields_by_name),
            await self._owner_filter(current_user),
        )

        # 投影列表 + 详情所需的全部字段：列表宽一点换来详情抽屉零请求秒开，
        # 而按 record_id 单取一条要 3~5 秒（飞书要算 178 字段里的公式/查找列）。
        #
        # project_all 的工作台（威达）改成投影**全字段**：用户自定义索引页可以挑表里任意字段，
        # 只投预设分组的话那些字段的值根本不会返回。按钮(3001)没有值，跳过。
        if cfg.project_all:
            projected = [f for f, t in fields_by_name.items() if t != 3001]
        else:
            projected = [f for f in detail_field_names(cfg.key) if f in fields_by_name]
        # 全部关联字段都要抽 link_ids，不能只抽合并那两个：详情页要按「航线报价」的关联 id
        # 去拉那几条报价记录。写死两个字段的话，这类关联的 id 压根不出现在返回里。
        link_fields = frozenset({n for n, t in fields_by_name.items() if t in LINK_TYPES})

        # 排序字段同样要过一遍存在性校验：字段被改名时，飞书会对整个请求报 400，
        # 宁可丢掉排序也不能让整页打不开。
        sort_spec = _build_sort(cfg, fields_by_name)

        # 整页缓存：前端一次要拉全量（5 页 × 5~6 秒），刷新/切工作台/重进页面都会重打飞书。
        # 键里带 filter（含关键字/待办/行级权限）、游标、页大小与写操作版本号。
        version = await self._list_version()
        cache_key = self._list_cache_key(role, filter_obj, page_token, page_size, version)
        try:
            cached = await self._redis.get(cache_key)
        except Exception as e:
            log.warning(f"读工作台列表缓存失败: {e!s}")
            cached = None
        if cached:
            try:
                # Redis 客户端开了 decode_responses，二进制会在这里炸；
                # 所以存的是 base64（纯 ASCII），压缩只在内存里做。
                packed = cached.encode("ascii") if isinstance(cached, str) else cached
                page = json.loads(zlib.decompress(base64.b64decode(packed)).decode("utf-8"))
            except Exception:
                log.warning("工作台列表缓存损坏，忽略并重新拉取")
            else:
                if isinstance(page, dict) and "items" in page:
                    return page
                log.warning(f"工作台列表缓存结构不符，忽略: {type(page)}")

        data = await self._search_page(
            filter_obj,
            page_token,
            page_size,
            field_names=projected or None,
            sort=sort_spec,
        )
        items = [
            self._normalize_record(item, fields_by_name, link_fields)
            for item in (data.get("items") or [])
        ]
        # 关联字段补上对方的名字（飞书只给 id，见 `_fill_link_texts`）
        await self._fill_link_texts(items, link_targets)
        if cfg.single_status:
            await self._fill_single_status(items, link_targets.get(SINGLE_STATUS_ORDER_FIELD, ""))
        # `cfg.related` 那几张从表**不在这里拉**：它们只在详情抽屉里显示，点到那个 Tab 才取
        # （见 `linked_records` 的反查分支）。索引页为此多跑一次从表整表扫描，换来的只是列表
        # 变慢——列表上现在已经没有从表那一列了。
        has_more = bool(data.get("has_more"))
        next_token = str(data.get("page_token") or "") or None
        result = {
            "items": items,
            "total": int(data.get("total") or 0),
            "page_token": next_token if has_more else None,
            "has_more": has_more,
            "pending": pending,
        }
        try:
            packed = base64.b64encode(
                zlib.compress(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            ).decode("ascii")
            await self._redis.set(name=cache_key, value=packed, ex=_LIST_CACHE_TTL)
        except Exception as e:
            log.warning(f"写工作台列表缓存失败（不影响本次返回）: {e!s}")
        if _prefetch and has_more and not page_token:
            # 冷启动首页返回后，后台把剩下几页也拉进缓存。前端接着请求第 2~5 页时
            # 就能命中，从「每页 5~7 秒」降到几十毫秒；代价是每次冷窗口多打几次飞书。
            asyncio.create_task(
                self._prefetch_pages(
                    role, pending, next_token, page_size, search, current_user
                )
            )
        return result

    async def _prefetch_pages(
        self,
        role: str,
        pending: str | None,
        token: str | None,
        page_size: int,
        search: str | None,
        current_user: Any | None,
    ) -> None:
        """后台顺游标预取剩余页，仅填缓存，失败静默（不影响任何请求）。"""
        try:
            for _ in range(50):
                if not token:
                    return
                page = await self.list_workbench(
                    role, pending, token, page_size, search, current_user, _prefetch=False
                )
                token = page.get("page_token")
        except Exception as e:
            log.warning(f"工作台后台预取中断: {e!s}")

    async def get_workbench_record(
        self,
        record_id: str,
        role: str = "customer_service",
        invoice_no: str | None = None,
        current_user: Any | None = None,
    ) -> dict[str, Any]:
        """单条记录详情。

        ``GET /records/{id}`` 会返回全部 178 个字段并触发公式/查找列计算，实测要 3~5 秒；
        所以能用发票号定位时改走 ``records/search``（带字段裁剪，约 1 秒），
        拿不到发票号（票据本身没填）时才退回 ``batch_get``。

        ``current_user`` 有值时按 ``cfg.owner_field`` 校验这条记录归不归你（见 `_assert_owner`）。
        """
        cfg = get_workbench(role)  # 校验角色合法
        fields_by_name = await self._field_types()
        link_fields = frozenset({MERGE_MASTER_FIELD, MERGE_HAWB_FIELD})
        # 详情必须单独判一次：列表被过滤掉不代表详情也进不来——record_id 是可以从
        # 别处（分享链接、翻旧页、或者干脆手拼）拿到的，接口这一层不判就等于没权限。
        owner_cond = await self._owner_filter(current_user)
        owner_open_id = str(owner_cond["value"][0]) if owner_cond else ""

        if invoice_no:
            data = await self._search_page(
                {"conjunction": "and", "conditions": [_is_condition("发票号", invoice_no)]},
                None,
                1,
                field_names=[f for f in detail_field_names(cfg.key) if f in fields_by_name] or None,
            )
            item = next(iter(data.get("items") or []), None)
            if item:
                record = self._normalize_record(item, fields_by_name, link_fields)
                self._assert_owner(record, owner_open_id)
                return record

        data = await self._send(
            "POST",
            await self._endpoint("/records/batch_get"),
            json={"record_ids": [record_id]},
        )
        _check_lark_error(data, "查询记录详情")
        item = next(iter((data.get("data") or {}).get("records") or []), None)
        if not item:
            raise CustomException(msg="记录不存在或已被删除", status_code=status.HTTP_404_NOT_FOUND)
        record = self._normalize_record(item, fields_by_name, link_fields)
        self._assert_owner(record, owner_open_id)
        return record

    def _assert_owner(self, record: dict[str, Any], owner_open_id: str) -> None:
        """详情越权兜底：``owner_open_id`` 为空（= 不限制）直接放行，否则必须是本人。

        按 open_id 比而不是按姓名比：姓名可改、可重名，open_id 不会。
        """
        if not owner_open_id:
            return
        cfg = self._cfg
        field = cfg.owner_field if cfg else ""
        refs = (record.get("user_refs") or {}).get(field) or []
        if not any(str(r.get("id")) == owner_open_id for r in refs):
            raise CustomException(msg="这条记录不属于你，无法查看", status_code=status.HTTP_403_FORBIDDEN)

    async def _prepare_write(self, fields: dict[str, Any], table_id: str = "") -> dict[str, Any]:
        """把前端字段值还原为飞书写入格式（只读字段直接跳过）。

        ``table_id`` 非空即写**另一张表**（详情页就地改关联的报价/方案），类型表也跟着换。
        """
        types = await self._field_types_of(table_id)
        payload: dict[str, Any] = {}
        for name, value in fields.items():
            ft = types.get(name)
            if ft is None or ft in READONLY_TYPES:
                continue
            payload[name] = denormalize_field_value(ft, value)
        return payload

    async def _batch_update(self, updates: list[dict[str, Any]], action: str, table_id: str = "") -> int:
        """批量写回飞书（``records/batch_update``，分批以避开单次上限）。

        写完顺手清掉**被写的这张表**的订单状态索引缓存：核价/交接这些字段正是「单票状态」
        的两个输入，不清的话本系统刚写进去的改动要等一个 TTL 才在列表标签上出现
        （见 `_ORDER_STATUS_TTL`）。``table_id`` 为空即写工作台自己的表。
        """
        if not updates:
            return 0
        url = await (self._table_endpoint(table_id, "/records/batch_update") if table_id else self._endpoint("/records/batch_update"))
        written = 0
        for start in range(0, len(updates), _BATCH_SIZE):
            chunk = updates[start : start + _BATCH_SIZE]
            data = await self._send("POST", url, json={"records": chunk})
            _check_lark_error(data, action)
            written += len((data.get("data") or {}).get("records") or chunk)
        await self._invalidate_order_status(table_id or self._table_id)
        return written

    async def _link_ids_of(self, record_ids: list[str], field: str) -> dict[str, list[str]]:
        """读若干条记录在指定关联字段上的 record_id 列表。"""
        if not record_ids:
            return {}
        types = await self._field_types()
        if field not in types:
            raise CustomException(msg=f"飞书表中不存在关联字段「{field}」，无法计算合并组")
        result: dict[str, list[str]] = {}
        for start in range(0, len(record_ids), _BATCH_SIZE):
            chunk = record_ids[start : start + _BATCH_SIZE]
            data = await self._send(
                "POST",
                await self._endpoint("/records/batch_get"),
                json={"record_ids": chunk},
            )
            _check_lark_error(data, "查询记录")
            for item in (data.get("data") or {}).get("records") or []:
                rid = str(item.get("record_id") or "")
                if rid:
                    result[rid] = _links_to_ids((item.get("fields") or {}).get(field))
        return result

    async def _expand_merge_groups(
        self, record_ids: list[str], link_field: str
    ) -> dict[str, set[str]]:
        """按关联字段还原若干票所在的合并组，返回「组根 → 成员集合」。

        组的成员里含自身，所以孤票会自成一元组——调用方据此区分「本来就在某个组里」
        与「没有组可维护」。跨组重组与拆出都先走这一步拿到完整的旧组。
        """
        ids = sorted(set(record_ids))
        if not ids:
            return {}
        current = await self._link_ids_of(ids, link_field)
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            root = x
            while parent[root] != root:
                root = parent[root]
            # 路径压缩
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for rid in ids:
            find(rid)
        for rid, peers in current.items():
            find(rid)
            for pid in peers:
                union(rid, pid)

        groups: dict[str, set[str]] = {}
        for rid in list(parent):
            groups.setdefault(find(rid), set()).add(rid)
        return groups

    async def update_workbench_record(
        self,
        record_id: str,
        fields: dict[str, Any],
        role: str = "customer_service",
        sync_group: bool = False,
    ) -> dict[str, Any]:
        """更新单条记录。

        ``sync_group=True`` 时，订舱/计划类字段（``MERGE_SYNC_FIELDS``）会连带写到同
        合并组的其他票——对应「勾选订舱单联动整组」的既有业务习惯；其余字段只写当前票。
        """
        cfg = get_workbench(role)  # 校验角色合法
        # 操作工作台单票单同步：即使调用方传了 True 也只写当前票
        sync_group = sync_group and cfg.allow_group_sync
        payload = await self._prepare_write(fields)
        if not payload:
            raise CustomException(msg="更新失败：没有可写入的字段")

        updates: list[dict[str, Any]] = [{"record_id": record_id, "fields": payload}]
        shared = {k: v for k, v in payload.items() if k in MERGE_SYNC_FIELDS}
        if sync_group and shared:
            peers = await self._group_peer_ids(record_id)
            updates.extend({"record_id": pid, "fields": shared} for pid in peers if pid != record_id)

        await self._batch_update(updates, "更新记录")
        await self._bump_list_version()
        return {"record_id": record_id, "written": len(updates), "group_synced": len(updates) - 1}

    def _form_cache_key(self, role: str) -> str:
        """新建表单定义的缓存键。

        带 ``app_token`` 与**表单视图 id**：视图 id 是每个工作台各自配的
        （``create_form_view_id``），两个工作台共用同一个 base 时不能互相拿到对方的表单。
        """
        cfg = get_workbench(role)
        view_id = cfg.create_form_view_id if cfg else ""
        return f"lark_form:v{_FORM_CACHE_VERSION}:{self._app_token}:{view_id or role}"

    async def get_create_form(self, role: str = "customer_service") -> list[dict[str, Any]]:
        """读飞书**表单视图**的定义：新建抽屉照它渲染。

        业务在飞书里维护这个表单（哪几个字段、什么顺序、哪个必填、标题与说明怎么写），
        这里只做一次翻译，**不在代码里复制一份字段表**——否则业务一改表单，两边就分叉了。

        表单问题只带 ``field_id``，得用原始字段定义换回字段名；字段被删但表单里还留着
        条目时跳过（飞书不会自动清理）。

        **结果进 Redis（``_FORM_CACHE_TTL``）**：表单定义在飞书里改一次要用很久，而这里
        每次点开「新建」都要翻页取一遍。缓存按视图 id 分键，TTL 一过自然重取。

        返回:
        - list[dict]: 按表单顺序的字段定义（含 required/description/type/editable 等）。
        """
        cfg = get_workbench(role)  # 校验角色合法
        view_id = cfg.create_form_view_id
        if not view_id:
            return []

        rc = RedisCURD(self._redis)
        cache_key = self._form_cache_key(role)
        cached = await rc.get(cache_key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8") if isinstance(cached, bytes) else str(cached))
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书新建表单缓存损坏，忽略并重新拉取")
            else:
                if isinstance(raw, list):
                    return raw

        url = await self._endpoint(f"/forms/{view_id}/fields")
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = await self._send("GET", url, params=params)
            _check_lark_error(data, "查询表单")
            body = data.get("data") or {}
            items.extend(body.get("items") or [])
            if not body.get("has_more"):
                break
            page_token = str(body.get("page_token") or "") or None
            if not page_token:
                break

        by_id = {str(f.get("field_id") or ""): f for f in await self._raw_field_items()}
        metas = {m.name: m for m in await self.get_fields()}
        defaults = await self._user_defaults(cfg)
        form: list[dict[str, Any]] = []
        for item in items:
            if not item.get("visible"):
                continue
            raw = by_id.get(str(item.get("field_id") or ""))
            if raw is None:
                continue
            name = str(raw.get("field_name") or "")
            meta = metas.get(name)
            ft = int(raw.get("type", 0))
            control = _form_control(ft)
            prop = raw.get("property") or {}
            form.append(
                {
                    "name": name,
                    "title": str(item.get("title") or name),
                    "required": bool(item.get("required")),
                    "description": str(item.get("description") or ""),
                    "type": ft,
                    "ui_type": str(raw.get("ui_type") or ""),
                    # 前端照 control 挂控件，别再自己从 type 推一遍——「什么类型对应什么控件」
                    # 这件事只该在一个地方定义
                    "control": control,
                    "editable": control != "readonly",
                    "options": list(meta.options) if meta else [],
                    # 关联字段指向的表：前端据此拉候选记录做选择器
                    "link_table_id": str(prop.get("table_id") or "") if ft in LINK_TYPES else "",
                    # 人员字段能不能多选由飞书字段属性说了算（本表「负责航线」multiple=true、
                    # 「商机负责人」false）。单选字段送两个人会被飞书整单拒掉，所以选择器
                    # 必须跟着它走，不能一律放开。其余控件不受影响。
                    "multiple": bool(prop.get("multiple")) if control == "user" else True,
                    # 预选值：只有配了 user_defaults 的字段非空（如「负责航线」默认那两位）
                    "default": list(defaults.get(name, [])),
                }
            )
        await rc.set(key=cache_key, value=json.dumps(form, ensure_ascii=False), expire=_FORM_CACHE_TTL)
        return form

    async def _department_children(self, dept_id: str) -> list[str]:
        """一个部门的**直属**子部门 id（翻完页）。"""
        children: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "page_size": _CONTACT_PAGE_SIZE,
                "department_id_type": "open_department_id",
            }
            if page_token:
                params["page_token"] = page_token
            data = await self._send(
                "GET", _open_api(f"/contact/v3/departments/{dept_id}/children"), params=params
            )
            _check_lark_error(data, "查询部门")
            body = data.get("data") or {}
            for item in body.get("items") or []:
                # 传什么 id 类型就得用什么 id 类型取：这里统一用 open_department_id，
                # 混用 department_id 会命中另一个部门（或直接报不存在）
                child = str(item.get("open_department_id") or "")
                if child:
                    children.append(child)
            if not body.get("has_more"):
                break
            page_token = str(body.get("page_token") or "") or None
            if not page_token:
                break
        return children

    async def _contact_department_ids(self) -> list[str]:
        """广度优先爬出**全部**部门 id（含根部门 ``0``）。

        必须递归：飞书的成员列表接口只返回**直属**成员，而根部门下常常没几个人——
        威达根部门直属 1 人、其余 17 人全在子部门里。只看根部门会得到一个几乎空的人员
        下拉，表现为「通讯录明明有人却选不到」。

        **按层并发**：同一层的部门互相独立，其子部门查询一起发。树的深度才是这里的
        延迟来源（实测 3 层 ≈ 1.2s），而部门总数不是——原来逐个串行时，光是这一趟
        爬树就吃掉 9s，全都算在用户点「新建」后的等待里。
        """
        ids: list[str] = ["0"]
        seen: set[str] = {"0"}
        level: list[str] = ["0"]
        gate = asyncio.Semaphore(_CONTACT_CONCURRENCY)

        async def fetch_children(dept_id: str) -> list[str]:
            async with gate:
                return await self._department_children(dept_id)

        while level and len(ids) < _CONTACT_DEPT_MAX:
            batches = await asyncio.gather(*(fetch_children(d) for d in level))
            level = []
            for children in batches:
                for child in children:
                    if child in seen:
                        continue
                    seen.add(child)
                    ids.append(child)
                    level.append(child)
        return ids

    async def _department_users(self, dept_id: str) -> dict[str, str]:
        """一个部门的**直属**成员，返回 ``{open_id: 显示名}``（翻完页）。"""
        users: dict[str, str] = {}
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "page_size": _CONTACT_PAGE_SIZE,
                "department_id": dept_id,
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
            }
            if page_token:
                params["page_token"] = page_token
            data = await self._send("GET", _open_api("/contact/v3/users"), params=params)
            _check_lark_error(data, "查询通讯录")
            body = data.get("data") or {}
            for item in body.get("items") or []:
                open_id = str(item.get("open_id") or "")
                if open_id:
                    # 没有中文名的人（如外籍同事）拿英文名兜底，再没有才显示 id，
                    # 总比下拉里出现一个空白选项强
                    users[open_id] = str(item.get("name") or item.get("en_name") or open_id)
            if not body.get("has_more"):
                break
            page_token = str(body.get("page_token") or "") or None
            if not page_token:
                break
        return users

    async def _load_contact_users(self) -> list[dict[str, str]]:
        """从飞书通讯录拉全部成员（未走缓存），返回 ``[{open_id, name}]``。

        每个部门各查一次并翻页，最后按 open_id 去重——一个人可以同时在多个部门里。
        这些查询**互不依赖**，所以一起发（走 ``_CONTACT_CONCURRENCY`` 限流），
        而不是排成一列慢慢等。
        """
        depts = await self._contact_department_ids()
        gate = asyncio.Semaphore(_CONTACT_CONCURRENCY)

        async def one(dept_id: str) -> dict[str, str]:
            async with gate:
                return await self._department_users(dept_id)

        users: dict[str, str] = {}
        for batch in await asyncio.gather(*(one(d) for d in depts)):
            users.update(batch)
        return [{"open_id": oid, "name": name} for oid, name in sorted(users.items(), key=lambda kv: kv[1])]

    async def _contact_directory(self) -> list[dict[str, str]]:
        """带缓存的**完整**通讯录（``[{open_id, name}]``，不含任何工作台的人员偏好）。

        缓存是租户级的（键里只有 app_id），两个 base 共用一份——所以排除名单之类
        「某张表的偏好」不能写进来，得由调用方在下发前过滤。
        """
        app_id = _require_credentials()[0]
        cache_key = f"lark_contact_users:{app_id}"
        rc = RedisCURD(self._redis)
        cached = await rc.get(cache_key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8") if isinstance(cached, bytes) else str(cached))
                return [{"open_id": str(u["open_id"]), "name": str(u["name"])} for u in raw]
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                log.warning("飞书通讯录缓存损坏，忽略并重新拉取")

        users = await self._load_contact_users()
        await rc.set(
            key=cache_key,
            value=json.dumps(users, ensure_ascii=False),
            expire=_CONTACT_CACHE_TTL,
        )
        return users

    async def list_user_options(
        self, search: str | None = None, role: str = "customer_service"
    ) -> list[dict[str, str]]:
        """通讯录成员：``[{open_id, name}]``，供新建表单的人员字段挑选。

        取的是**租户通讯录**而不是某张表：人员字段的值就是通讯录成员的 open_id
        （实测表里「负责航线」的 ``ou_...`` 与通讯录返回的完全一致），拿别的表当候选
        源反而会漏掉没在该表里出现过的人。整租户才十几个人，拉一次缓存十分钟。
        """
        cfg = get_workbench(role)  # 校验角色合法
        return _filter_users(await self._contact_directory(), search, cfg.hidden_users)

    async def _owner_filter(self, user: Any | None) -> dict[str, Any] | None:
        """行级数据权限：非超管只看 ``cfg.owner_field`` 等于自己的记录。

        返回可直接与其它条件 AND 的 filter，或 ``None``（= 不限制）。三种情况不限制：
        没配 ``owner_field`` 的工作台、没带用户信息（内部调用/脚本）、以及超管。

        认人靠**姓名**：拿 ``user.name`` 去通讯录里精确匹配同名成员换 ``open_id``。
        匹配不到**直接报错**而不是放行——fail-closed，否则一个改名/填错的账号会静默
        拿到全量数据，而那种问题从界面上完全看不出来（「怎么都能看到」不会有人报 bug）。
        """
        cfg = self._cfg
        if user is None or cfg is None or not cfg.owner_field:
            return None
        if bool(getattr(user, "is_superuser", False)):
            return None

        name = str(getattr(user, "name", "") or "").strip()
        if not name:
            raise CustomException(msg="当前账号没有昵称，无法确定你在飞书里是谁，请联系管理员")

        directory = {u["name"]: u["open_id"] for u in await self._contact_directory()}
        open_id = directory.get(name)
        if not open_id:
            raise CustomException(
                msg=f"当前账号昵称「{name}」在飞书通讯录里没有同名成员，看不到数据；请联系管理员核对"
            )
        return _is_condition(cfg.owner_field, open_id)

    async def _user_defaults(self, cfg: WorkbenchConfig) -> dict[str, list[str]]:
        """把配置里的默认人（姓名）换成 open_id，字段名 → id 列表。

        配置写姓名是为了给人维护（一串 ``ou_...`` 没人知道是谁），而控件认的是 id，
        所以在这一层换一次。换不到的（改了名、离职、或被 ``hidden_users`` 挡掉）跳过并
        记日志——静默少一个默认值比报错更难查。
        """
        if not cfg.user_defaults:
            return {}
        directory = {u["name"]: u["open_id"] for u in await self.list_user_options(role=cfg.key)}
        defaults: dict[str, list[str]] = {}
        for field_name, names in cfg.user_defaults.items():
            ids = [directory[n] for n in names if n in directory]
            missing = [n for n in names if n not in directory]
            if missing:
                log.warning(f"人员默认值在通讯录里找不到，已跳过：{missing}（字段「{field_name}」{cfg.key}）")
            if ids:
                defaults[field_name] = ids
        return defaults

    async def _list_raw_records(
        self, table_id: str, limit: int, field_names: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """读另一张表的原始记录（关联字段的候选列表用），最多取 ``limit`` 条。

        **走 ``records/search``（POST）而不是 ``GET /records``**。同一张客商表、同样的 193 行，
        实测 GET 全列 **20.85s**（``page_size=100`` 那档直接 30s 超时），search 全列 1.39s、
        投影后 **0.97s**——差 20 倍以上。而「新建」抽屉每点一次关联字段就要扫一遍这张表，
        那正是「新建商机要等很久 / 等不到只能刷新」的来源。两条路的约束是同一套
        （``page_size`` 必须走 URL query，见 ``_search_page``）。

        ``field_names`` 传了即只取这几列：候选列表只要主字段。**投影省下的不只是流量**——
        它让飞书不必现算那些公式/查找/双向关联列，那 20 倍里的大头在这里
        （客商表 15 列里有 3 列是这类，主字段本身就是公式）。
        """
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(items) < limit:
            data = await self._search_page(
                None,
                page_token,
                min(500, limit - len(items)),
                field_names=field_names,
                table_id=table_id,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "") or None
            if not page_token:
                break
        return items

    async def _primary_field(self, table_id: str) -> tuple[str, int] | None:
        """目标表主字段的 ``(字段名, 类型)``；表里没有主字段时返回 None。

        关联字段要显示出名字，靠的都是目标表的这一列：主字段就是「这条记录叫什么」。
        """
        primary = next((m for m in await self._table_fields(table_id) if m.is_primary), None)
        if primary is None:
            return None
        return primary.name, primary.type

    async def _link_text_map(self, table_id: str) -> dict[str, str]:
        """某张表的主字段显示文本：``record_id → 文本``（Redis 缓存）。

        飞书的记录接口**只回关联的 record_id，不回对方的名字**，所以「客户」「供应商」
        这类关联字段要显示出名字，只能反过来拿 id 去目标表查一遍。一张表在一次请求里
        可能被问很多次（列表页一页 500 行挂的都是同一个客户表），几分钟内又不会变，
        所以缓存住。

        只投影**主字段一列**：目标表可能有几百行、上百列，整表拉回来纯属浪费——本表
        实测投影成单列后响应小两个数量级，而这里本来也只要这一列。
        """
        key = f"lark_linktexts:{self._app_token}:{table_id}"
        rc = RedisCURD(self._redis)
        cached = await rc.get(key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8") if isinstance(cached, bytes) else str(cached))
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书关联文本缓存损坏，忽略并重新拉取")
            else:
                if isinstance(raw, dict):
                    return {str(k): str(v) for k, v in raw.items()}

        mapping: dict[str, str] = {}
        primary = await self._primary_field(table_id)
        if primary is not None:
            primary_name, primary_type = primary
            page_token: str | None = None
            while len(mapping) < _LINK_OPTION_MAX:
                data = await self._search_page(
                    None,
                    page_token,
                    min(500, _LINK_OPTION_MAX - len(mapping)),
                    field_names=[primary_name],
                    table_id=table_id,
                )
                for record in data.get("items") or []:
                    record_id = str(record.get("record_id") or "")
                    if record_id:
                        value = normalize_field_value(primary_type, (record.get("fields") or {}).get(primary_name))
                        mapping[record_id] = str(value or "")
                if not data.get("has_more"):
                    break
                page_token = str(data.get("page_token") or "") or None
                if not page_token:
                    break

        await rc.set(key=key, value=json.dumps(mapping), expire=_LINK_TEXT_TTL)
        return mapping

    async def _fill_link_texts(self, records: list[dict[str, Any]], link_targets: dict[str, str]) -> None:
        """把记录里关联字段的值补成**对方的名字**（就地改 ``fields``）。

        飞书不给名字，所以 ``fields['客户']`` 原本是 null——详情页上这些格子会整片空白，
        而飞书自己的面板是有名字的。查不到某个 id 时退回显示 record_id：宁可露出一串
        id，也不要静默留空（空着和「这个字段本来就没值」长得一模一样）。
        """
        # 只查**这一批记录里真的用到了**的表：本表 5 个关联字段，一页记录里往往只有
        # 两三个挂着值，为没用到的那几张表各跑一趟纯属白等
        targets = [
            (name, tid)
            for name, tid in link_targets.items()
            if tid and any((record.get("link_ids") or {}).get(name) for record in records)
        ]
        if not targets:
            return
        # 几张表并行查：串行等 4 张表就是 4 个来回，而这些查询彼此无关
        maps = await asyncio.gather(*[self._link_text_map(tid) for _name, tid in targets])
        for (name, _tid), texts in zip(targets, maps, strict=True):
            for record in records:
                ids = (record.get("link_ids") or {}).get(name) or []
                if ids:
                    record["fields"][name] = [texts.get(rid) or rid for rid in ids]

    async def _fill_single_status(self, records: list[dict[str, Any]], order_table: str) -> None:
        """给每条记录算一个「单票状态」（顶层 ``single_status`` 键，就地改）。

        **不塞进 ``fields``**：`fields` 是「飞书字段名 → 值」的映射，前端会拿它去查字段元数据
        （`metaOf(name)`）决定怎么渲染；凭空多一个没有 ``FieldMeta`` 的键，轻则渲染成空白格，
        重则被 `WeidaColumnConfig` 当成可选列列出来——一个不存在的「字段」。状态是派生值，
        放顶层另走一条渲染路径（`WeidaStatusTag`）。

        订单表那一侧取不到（没关联订单 / 表 id 解析不出来）时按「没有订单」算：订单表上没有
        记录，`负责航线已核价` 与 `交接给国内操作` 都视作未完成，状态照商机自身的字段给。
        """
        order_ids: set[str] = set()
        for record in records:
            order_ids.update((record.get("link_ids") or {}).get(SINGLE_STATUS_ORDER_FIELD) or [])
        status_map = await self._order_status_map(order_table) if (order_table and order_ids) else {}

        for record in records:
            ids = (record.get("link_ids") or {}).get(SINGLE_STATUS_ORDER_FIELD) or []
            # 一单一张订单：多张时只要有一张走到「已核价/已交接」就算走到了，
            # 取「任一」而不是「第一张」——顺序由飞书给，不可靠
            flags = [status_map[rid] for rid in ids if rid in status_map]
            fields = record.get("fields") or {}
            quote_ids = (record.get("link_ids") or {}).get(SINGLE_STATUS_QUOTE_FIELD) or []
            record["single_status"] = _single_status(
                quote_has=bool(quote_ids),
                got=str(fields.get(SINGLE_STATUS_GOT_FIELD) or ""),
                note=str(fields.get(SINGLE_STATUS_NOTE_FIELD) or ""),
                priced=any(priced for priced, _ in flags),
                handover=any(handover for _, handover in flags),
            )

    async def _related_blocks(self, related: tuple[RelatedTable, ...]) -> list[RelatedBlockSchema]:
        """详情抽屉里那几页**从表**（见 `RelatedTable`）：每页一个标识 + 名字 + 要铺的字段元数据。

        元数据取自从表**真的**字段定义（类型/选项/说明照抄），**可编辑性与那张表保持一致**：
        这几行虽然由飞书侧的自动化写进来（订单上的「创建iFreight 数据表」、iFreight 上的
        「同步到iFreight」两个按钮），但改起来就是普通的一格——本系统的写入通道（`update_linked_record`）
        直接写回从表，字段类型也是照那份定义归一化的，没有二次同步这回事（飞书的自动化只在
        按按钮/建行时跑一次，不会回头覆盖）。

        唯一的改动是 `is_primary=False`：从表的主字段名摆在这一页里也不该被当成「记录的名字」
        （`linkedTitle` 之类靠它取标题，而这里一行是别人——不是当前这条记录）。

        从表里被公式/查找/自动编号接管的字段仍是只读——那是**飞书侧**的只读，不是本系统的取舍。
        """
        blocks: list[RelatedBlockSchema] = []
        for rel in related:
            by_name = {m.name: m for m in await self._table_fields(rel.table_id)}
            fields: list[FieldMetaSchema] = []
            for name in rel.fields:
                meta = by_name.get(name)
                if meta is None:
                    # 从表改过名：跳过（少一格），但记一条警告——静默少一格和「这格本来就是空的」
                    # 长得一模一样
                    log.warning(f"从表 {rel.table_id} 里没有字段「{name}」（{rel.key} 这一页已跳过它）")
                    continue
                fields.append(meta.model_copy(update={"is_primary": False}))
            blocks.append(RelatedBlockSchema(key=rel.key, label=rel.label, fields=fields))
        return blocks

    def _link_option_cache_key(
        self,
        parent_table: str,
        field_name: str,
        filter_field: str,
        filter_values: list[str] | None,
    ) -> str:
        """候选记录的缓存键（存的是**未按关键字筛过**的那一份）。

        过滤条件（``filter_field``/``filter_values``）也要进键：同一张表上可能有两个
        关联字段指向同一个候选表，一个只要「订舱代理」、一个全要——共用一份缓存等于
        把筛选条件也共用掉，下拉里会混进不该有的选项。
        """
        wanted = ",".join(sorted(v for v in (filter_values or []) if v))
        return (
            f"lark_linkopt:v{_LINK_OPTION_CACHE_VERSION}:{self._app_token}:"
            f"{parent_table}:{field_name}:{filter_field}:{wanted}"
        )

    async def _collect_link_options(
        self,
        target: str,
        primary: FieldMetaSchema,
        metas: dict[str, FieldMetaSchema],
        filter_field: str,
        wanted: frozenset[str],
    ) -> list[dict[str, Any]]:
        """实拉关联表的候选记录。**不做关键字筛选**——那一层在内存里做（见调用方），
        混进来缓存就得随每个字变一份。"""
        # 只投影用得上的列：主字段（候选的显示文本）+ 过滤列（有的话，本地筛要用它的值）
        projection = [primary.name] + ([filter_field] if wanted else [])
        options: list[dict[str, Any]] = []
        for record in await self._list_raw_records(target, _LINK_OPTION_MAX, projection):
            fields = record.get("fields") or {}
            if wanted and not _value_in(
                normalize_field_value(metas[filter_field].type, fields.get(filter_field)), wanted
            ):
                continue
            text = str(normalize_field_value(primary.type, fields.get(primary.name)) or "")
            record_id = str(record.get("record_id") or "")
            if record_id:
                options.append({"record_id": record_id, "text": text})
        return options

    async def list_link_options(
        self,
        field_name: str,
        search: str | None = None,
        role: str = "customer_service",
        table_id: str = "",
        filter_field: str = "",
        filter_values: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """关联字段的候选记录：``[{record_id, text}]``，``text`` 取目标表的主字段。

        关键字在**本地**过滤而不是推给飞书：候选表通常只有一两百行（客户表 192 行），
        一次拉回来当场筛，比给每次输入都发一个 ``records/search`` 简单也快得多。

        **候选清单进 Redis，但只存 ``_LINK_OPTION_CACHE_TTL``（本文件最短的时长）**：
        这里原来的做法是每次都实拉（因为「挑一条来关联」最怕「刚在飞书里新建的客户
        在下拉里选不到」，而 ``_link_text_map`` 那种 300 秒的缓存正会这样），但整表拉一次
        实测最慢 19.6s，而「新建」抽屉每点开一次就要一整套——半小时的滞后受不了，
        是半小时的卡顿也受不了。故取「连点几次都秒开、新增的客户最多晚半分钟出现」。
        关键字（``search``）**不进缓存**：它筛的是同一份候选，进键只会让缓存永远命不中。

        ``table_id`` 是**父表**（字段长在哪张表上），留空即本工作台的表；见 `_link_target_table`。
        二级关联要传：订单表的「供应商」长在订单表上，只扫工作台表会得到「表里不存在该字段」。

        ``filter_field`` / ``filter_values`` 是**候选本身的业务约束**，同样在本地筛：
        订单表的「供应商」指向整张客商表，但业务上只有「客商角色」含**订舱代理**的那些
        才是候选——不筛的话下拉里会混进客户、报关行、车队。给空值即不筛。
        """
        get_workbench(role)  # 校验角色合法
        # 缓存键要在**解析目标表之前**算出来（解析本身就是一次请求），所以用「父表 + 字段名」
        # 定位——它俩加上 ``app_token`` 已经唯一确定了目标表，不必先解析出来。
        rc = RedisCURD(self._redis)
        cache_key = self._link_option_cache_key(
            table_id or self._table_id, field_name, filter_field, filter_values
        )
        candidates: list[dict[str, Any]] | None = None
        cached = await rc.get(cache_key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8") if isinstance(cached, bytes) else str(cached))
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书关联候选缓存损坏，忽略并重新拉取")
            else:
                if isinstance(raw, list):
                    candidates = raw

        if candidates is None:
            target = await self._link_target_table(field_name, table_id)
            metas = {m.name: m for m in await self._table_fields(target)}
            primary = next((m for m in metas.values() if m.is_primary), None)
            if primary is None:
                raise CustomException(msg="关联表没有主字段，列不出候选记录")

            wanted = frozenset(v for v in (filter_values or []) if v)
            if wanted and filter_field not in metas:
                # 字段被改名时宁可报出来，也不要静默退回「不过滤」——那会让下拉里重新混进不该有的选项
                raise CustomException(msg=f"关联表里没有字段「{filter_field}」，候选过滤无从谈起")

            candidates = await self._collect_link_options(target, primary, metas, filter_field, wanted)
            await rc.set(
                key=cache_key,
                value=json.dumps(candidates, ensure_ascii=False),
                expire=_LINK_OPTION_CACHE_TTL,
            )

        keyword = (search or "").strip().lower()
        if not keyword:
            return candidates
        # 关键字在**缓存下来的那一份**上本地筛：敲一个字不该把整表重新拉一遍
        return [opt for opt in candidates if keyword in str(opt.get("text") or "").lower()]

    def _order_status_cache_key(self, table_id: str) -> str:
        """订单状态索引的缓存键。带 app_token：两个 base 的表 id 可能撞名。"""
        return f"lark_orderstatus:{self._app_token}:{table_id}"

    async def _invalidate_order_status(self, table_id: str) -> None:
        """清掉某张表的订单状态索引缓存（本系统写完订单表后立即调）。

        不清的话，用户在本系统里点了「交接给国内操作」，回到列表看到的还是旧标签——
        而这类字段（核价/交接）恰恰是本系统的主要写入之一，60 秒的滞后在这条路上
        会被当成「没写进去」。
        """
        if table_id:
            await RedisCURD(self._redis).delete(self._order_status_cache_key(table_id))

    async def _order_status_map(self, table_id: str) -> dict[str, tuple[bool, bool]]:
        """订单表里判断「单票状态」要的那两列：``record_id → (已核价, 已交接)``。

        只投影这两列——整表拉回来是为了建索引（列表一页 50 条商机，逐条按 id 查要 50 次
        请求，而飞书单取一条还要算全部公式，见 `linked_records` 的说明）。订单表才几十行，
        投影后一次请求就够。

        **结果走 Redis 缓存**（`_ORDER_STATUS_TTL`）：`_fill_single_status` 每次列表都调它，
        实测这一次整表扫占列表总时长的一半（国内客服 2.73s 里 1.33s）。键里带表 id，
        写入侧（`_batch_update`）清缓存，见 `_ORDER_STATUS_TTL` 的注释。

        两个字段名在表里不存在时返回空映射：配置写的名字被改了，状态会一律算成「待航线报价」
        那样最前面的档，但**不能让整页打不开**——这跟 `_build_sort` 跳过缺失字段是同一个取舍。
        **这个分支不进缓存**：它是配置漂移的信号，缓存住只会让日志里的警告 60 秒才响一次。
        """
        key = self._order_status_cache_key(table_id)
        rc = RedisCURD(self._redis)
        cached = await rc.get(key)
        if cached:
            try:
                raw = json.loads(cached.decode("utf-8") if isinstance(cached, bytes) else str(cached))
            except (json.JSONDecodeError, TypeError, ValueError):
                log.warning("飞书订单状态缓存损坏，忽略并重新拉取")
            else:
                if isinstance(raw, dict):
                    # JSON 把元组变成数组，这里转回来——调用方按 (priced, handover) 解包
                    return {str(k): (bool(v[0]), bool(v[1])) for k, v in raw.items() if len(v) == 2}

        metas = {m.name: m for m in await self._table_fields(table_id)}
        wanted = [SINGLE_STATUS_PRICED_FIELD, SINGLE_STATUS_HANDOVER_FIELD]
        present = [name for name in wanted if name in metas]
        if not present:
            log.warning(f"订单表 {table_id} 里找不到「单票状态」要的字段（已跳过）: {wanted}")
            return {}
        result: dict[str, tuple[bool, bool]] = {}
        page_token: str | None = None
        while True:
            data = await self._search_page(None, page_token, 500, field_names=present, table_id=table_id)
            for item in data.get("items") or []:
                fields = item.get("fields") or {}
                priced_meta = metas.get(SINGLE_STATUS_PRICED_FIELD)
                priced = bool(
                    priced_meta
                    and normalize_field_value(priced_meta.type, fields.get(SINGLE_STATUS_PRICED_FIELD))
                    == SINGLE_STATUS_PRICED_VALUE
                )
                handover = bool(
                    normalize_field_value(
                        metas[SINGLE_STATUS_HANDOVER_FIELD].type,
                        fields.get(SINGLE_STATUS_HANDOVER_FIELD),
                    )
                )
                record_id = str(item.get("record_id") or "")
                if record_id:
                    result[record_id] = (priced, handover)
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "") or None
            if not page_token:
                break

        await rc.set(
            key=key,
            value=json.dumps({k: [v[0], v[1]] for k, v in result.items()}),
            expire=_ORDER_STATUS_TTL,
        )
        return result

    async def linked_records(
        self,
        field_name: str,
        record_ids: list[str],
        role: str = "customer_service",
        table_id: str = "",
    ) -> dict[str, Any]:
        """按关联字段取那几条记录（**连字段元数据一起**），供详情页展开关联分区。

        「商机 → 航线报价」这种一对多关联，详情页要显示的不只是对方的主字段（``list_link_options``
        只给这个），而是关联表**全部字段**的值，所以这里比 ``list_link_options`` 多返回一份
        ``fields``——前端拿到就能直接排版，不必再为「另一张表的字段有哪些」多发一次请求
        （那张表不属于任何工作台，``/config`` 也取不到它）。

        取法是**把关联表整表拉回来再按 id 筛**，不按 id 逐条查：飞书单取一条要算全部公式/查找
        （本表实测 3~5 秒），而整表一次才一秒出头；关联表都是几十上百行的小表。

        整表拉也要**投影成只要的那几列**：``/records``（不带 field_names）同样的 69 行要 4 秒，
        投影后 1.3 秒——差的是飞书把表里那些用不上的公式/查找也算了一遍。

        ``field_name`` 也认**反查页**（工作台在 `RelatedTable` 里配的，如国内操作的 iFreight）：
        那条边是反的——本表上没有指向从表的关联字段，从表反而有一列指回来。此时
        ``record_ids`` 传的是**本表记录**的 id（``back_ref_field`` 指着它的那些从表记录就是结果），
        目标表 id 也从配置里取（``_link_target_table`` 按字段名找不到这种字段）。

        返回值里带一份 ``table_id``：关联表不属于任何工作台，前端拿不到它的 id，而**下载这张表
        上的附件**偏偏需要它（飞书按 tableId 判权限，见 ``download_media``）。这里是前端唯一能
        学到这个 id 的地方。
        """
        cfg = get_workbench(role)  # 校验角色合法
        related = next((rel for rel in cfg.related if rel.key == field_name), None)
        if related is None:
            # 右边的 table_id 还是入参（= 父表），赋值后才是解析出来的**子表**——见 `_link_target_table`
            table_id = await self._link_target_table(field_name, table_id)
        else:
            # 反查页没有可解析的关联字段，表 id 只能从配置里拿
            table_id = related.table_id

        wanted = {rid for rid in record_ids if rid}
        if not wanted:
            return {"fields": [], "records": []}
        if related is not None:
            metas, records = await self._related_records(related, wanted)
            return {
                "table_id": table_id,
                "fields": [m.model_dump() for m in metas],
                "records": records,
            }

        metas = await self._table_fields(table_id)
        fields_by_name = {m.name: m.type for m in metas}
        # 按钮没有值，投影里带上它只是白占一行
        projected = [m.name for m in metas if m.type != 3001]
        # 关联表自己的关联字段也要抽 id：报价记录上挂着「关联商机」「被方案采用」，
        # 前端拿不到 id 就只能显示成文本，后续要跳转/回写都没有依据。
        link_fields = frozenset({m.name for m in metas if m.type in LINK_TYPES})
        # …而且这些字段还要补上名字，否则那几张卡片里这几格是空的——用户要的正是「一屏看全」。
        link_targets = {m.name: m.link_table_id for m in metas if m.link_table_id}

        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(items) < _LINKED_RECORD_MAX:
            data = await self._search_page(
                None,
                page_token,
                min(500, _LINKED_RECORD_MAX - len(items)),
                field_names=projected,
                table_id=table_id,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "") or None
            if not page_token:
                break

        records = [self._normalize_record(item, fields_by_name, link_fields) for item in items]
        records = [r for r in records if str(r.get("record_id") or "") in wanted]
        await self._fill_link_texts(records, link_targets)
        return {
            "table_id": table_id,
            "fields": [m.model_dump() for m in metas],
            "records": records,
        }

    async def _records_pointing_back(
        self,
        table_id: str,
        back_ref_field: str,
        names: set[str],
        wanted: set[str],
    ) -> tuple[list[FieldMetaSchema], list[dict[str, Any]]]:
        """从表里**指回** ``wanted`` 这些本表记录的那几条 + 用到的字段元数据。

        取法是**整表拉回来再按回指列筛**，不按 id 逐条查（飞书单取一条要算全部公式/查找，
        实测 3~5 秒，而整表一次一秒出头；从表都是几十上百行的小表）。也**不推给飞书筛**：
        回指列是关联字段，服务端 `contains`/`is` 实测比整表拉还慢（2.15s / 2.77s vs 1.71s）
        ——飞书照样要扫全表，还多花一趟。

        投影只留 ``names`` 点到的列（含回指列）：iFreight 有 93 个字段，多投一列就多让飞书
        算一次公式/查找。回指列**必须留着**——就靠它认出下游是谁。

        回指列**不进返回的字段元数据**（也不进 ``link_targets``）：它指的就是当前这条记录，
        这一页正长在那张记录的详情里，再摆一格「所属订单」是回声。筛选时要用它，所以它在
        投影里、也还在每条记录的 ``link_ids`` 里，只是不当作这一页的渲染字段。

        ``names`` 是**一次扫描服务的所有页的并集**（`related_blocks_records` 用）：几页共用
        一张表时，扫一遍各页再各自裁，比每页各扫一遍省下 N-1 次往返。
        """
        metas = [m for m in await self._table_fields(table_id) if m.name in {back_ref_field, *names}]
        if not any(m.name == back_ref_field for m in metas):
            # 回指列被改名/删掉了：认不出下游是谁，与其把**整张从表**当成结果返回（那会让每条
            # 订单都显示别人的 iFreight），不如空手而归并留一条日志
            log.warning(f"从表 {table_id} 里没有回指字段「{back_ref_field}」，这一批反查拿不到记录")
            return [], []
        fields_by_name = {m.name: m.type for m in metas}
        projected = [m.name for m in metas if m.type != 3001]
        link_fields = frozenset({m.name for m in metas if m.type in LINK_TYPES})
        link_targets = {m.name: m.link_table_id for m in metas if m.link_table_id}
        link_targets.pop(back_ref_field, None)

        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(items) < _LINKED_RECORD_MAX:
            data = await self._search_page(
                None,
                page_token,
                min(500, _LINKED_RECORD_MAX - len(items)),
                field_names=projected,
                table_id=table_id,
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "") or None
            if not page_token:
                break

        records = [self._normalize_record(item, fields_by_name, link_fields) for item in items]
        # 反查：从表里**指回**这些本表记录的，就是它们下游的那几条。一张订单挂两条 iFreight
        # 是常态（实测 16 张里 2 张如此），所以是筛出**一批**而不是挑一条。
        records = [r for r in records if _points_back_at(r, back_ref_field, wanted)]
        await self._fill_link_texts(records, link_targets)
        return [m for m in metas if m.name != back_ref_field], records

    async def _related_records(
        self, related: RelatedTable, wanted: set[str]
    ) -> tuple[list[FieldMetaSchema], list[dict[str, Any]]]:
        """单个反查页（`linked_records` 的反查分支）要的那几条 + 这一页的字段元数据。"""
        return await self._records_pointing_back(
            related.table_id,
            related.back_ref_field,
            {*related.fields},
            wanted,
        )

    async def related_blocks_records(
        self, record_ids: list[str], role: str = "customer_service"
    ) -> dict[str, dict[str, Any]]:
        """一次取回该工作台**全部反查页**的数据：``{页 key: {table_id, fields, records}}``。

        为什么要有这个接口：一个工作台的反查页常常是**同一张从表**（国内操作三页都指向
        iFreight、回指字段也同一个，区别只在铺哪几列），而逐页取数是各扫一遍全表——实测
        三个 Tab 合计 4.8s（1.93 + 1.54 + 1.31），其中两次纯属重复。这里按
        ``(表 id, 回指列)`` 分组，**一组只扫一次**（投影取该组各页字段的并集），扫完再按页
        裁列下发。三个 Tab 因此变成一次 1.4s 的请求，切页不再等。

        比在服务端加一层 TTL 缓存好在**零陈旧**：飞书侧（比如点「同步到iFreight」按钮）
        刚写的行，下一次打开抽屉就能看到，不用等缓存过期。

        没有配反查页的工作台返回空字典，调用方不必先查 `/config`。
        """
        cfg = get_workbench(role)  # 校验角色合法
        wanted = {rid for rid in record_ids if rid}
        if not cfg.related:
            return {}

        groups: dict[tuple[str, str], list[RelatedTable]] = {}
        for rel in cfg.related:
            groups.setdefault((rel.table_id, rel.back_ref_field), []).append(rel)

        out: dict[str, dict[str, Any]] = {}
        for (table_id, back_ref), rels in groups.items():
            names: set[str] = set()
            for rel in rels:
                names.update(rel.fields)
            if wanted:
                metas, records = await self._records_pointing_back(table_id, back_ref, names, wanted)
            else:
                # 没有要查的记录（理论上到不了：调用方不传 id 就没必要发这个请求），
                # 仍把每页的字段定义给出去，前端至少能画出空页而不是一片「加载中」。
                # 这一步不扫表——`_table_fields` 本身有缓存，代价只有一次字段清单。
                metas, records = [m for m in await self._table_fields(table_id) if m.name != back_ref], []
            for rel in rels:
                # 字段**清单**只给这一页点名的列（回指列是回声，见 `_records_pointing_back`）；
                # 但每条记录的 `fields`/`link_ids` 里**留着**回指列——逐页取数就是这么给的
                # （那边只把回指列从清单里摘掉，值还留在记录上），这里保持一致，
                # 免得前端拿到的记录形状在两条路径下不一样。
                out[rel.key] = {
                    "table_id": table_id,
                    "fields": [m.model_dump() for m in metas if m.name in set(rel.fields)],
                    "records": [_project_record(r, {back_ref, *rel.fields}) for r in records],
                }
        return out

    async def update_linked_record(
        self,
        field_name: str,
        record_id: str,
        fields: dict[str, Any],
        role: str = "customer_service",
        table_id: str = "",
    ) -> dict[str, Any]:
        """更新**关联表**里的一条记录（详情页展开的报价/方案卡片就地改）。

        与 ``update_workbench_record`` 只差一个「写在哪张表」：这里先拿关联字段问出目标表。
        **没有 ``sync_group``**——合并组联动是富士康那套「票与票」之间的关系，关联表里没这回事。

        ``field_name`` 也可以是一个**反查页** key（国内操作的 ``iFreight`` 数据页就地改）：
        那一页在本表上没有可解析的关联字段，目标表从工作台配置里取（见 `_related_page`）。

        ``table_id`` 是**父表**（字段长在哪张表上），留空即本工作台的表；见 `_link_target_table`。
        """
        get_workbench(role)  # 校验角色合法
        related = _related_page(role, field_name)
        table_id = related.table_id if related else await self._link_target_table(field_name, table_id)
        payload = await self._prepare_write(fields, table_id)
        if not payload:
            raise CustomException(msg="更新失败：没有可写入的字段")
        written = await self._batch_update([{"record_id": record_id, "fields": payload}], "更新记录", table_id)
        return {"record_id": record_id, "written": written}

    async def create_linked_record(
        self,
        field_name: str,
        fields: dict[str, Any],
        role: str = "customer_service",
        table_id: str = "",
    ) -> dict[str, Any]:
        """在**关联表**里新建一条记录（详情页「新增航线报价」）。

        回指字段（如报价上的「关联商机」）由**调用方放进 ``fields``**，这里不猜：回指哪个字段
        是页面的业务决定（航线报价回指商机、对客方案回指方案），写死一个字段名等于把这张表
        焊进服务层。没有它新记录就是个查不到、也没人认领的孤儿行。

        ``table_id`` 是**父表**（字段长在哪张表上），留空即本工作台的表；见 `_link_target_table`。
        ``field_name`` 也可以是一个反查页 key，见 `_related_page`。
        """
        get_workbench(role)  # 校验角色合法
        related = _related_page(role, field_name)
        table_id = related.table_id if related else await self._link_target_table(field_name, table_id)
        payload = await self._prepare_write(fields, table_id)
        if not payload:
            raise CustomException(msg="新建失败：没有可写入的字段")

        data = await self._send(
            "POST",
            await self._table_endpoint(table_id, "/records"),
            json={"fields": payload},
        )
        _check_lark_error(data, "新建记录")
        record_id = str(((data.get("data") or {}).get("record") or {}).get("record_id") or "")
        if not record_id:
            # 同 create_workbench_record：没回 ID 就定位不了这条记录，当失败报出来
            raise CustomException(msg="新建记录失败：飞书未返回记录 ID")
        log.info(f"飞书在关联表新建记录成功 {record_id}（{field_name}）")
        return {"record_id": record_id, "written": len(payload)}

    async def delete_linked_record(
        self,
        field_name: str,
        record_id: str,
        role: str = "customer_service",
        table_id: str = "",
    ) -> dict[str, Any]:
        """删掉**关联表**里的一条记录（对客方案费用明细的「删除」）。

        只删记录本身，不碰父记录上的关联字段：双向关联（21）由飞书自己维护另一侧，
        手动再去清一遍父记录的 ``link_ids`` 反而可能和飞书不同步。

        删除**不可逆**，所以调用方（前端）必须先二次确认——服务层不做这一层，
        它只负责「说了删就删」，把确认交给用户看得见的地方。

        ``table_id`` 是**父表**（字段长在哪张表上），留空即本工作台的表；见 `_link_target_table`。
        ``field_name`` 也可以是一个反查页 key，见 `_related_page`。
        """
        get_workbench(role)  # 校验角色合法
        related = _related_page(role, field_name)
        table_id = related.table_id if related else await self._link_target_table(field_name, table_id)
        data = await self._send(
            "DELETE",
            await self._table_endpoint(table_id, f"/records/{record_id}"),
        )
        _check_lark_error(data, "删除记录")
        log.info(f"飞书删除关联记录成功 {record_id}（{field_name}）")
        return {"record_id": record_id}

    async def create_workbench_record(self, fields: dict[str, Any], role: str = "customer_service") -> dict[str, Any]:
        """在目标表里新建一条记录。

        值走 ``_prepare_write`` 同一套归一化：只读字段（公式/查找/自动编号/按钮）
        直接跳过——它们由飞书自己算，送过去只会被拒。``fields`` 全被跳掉时**报错而不是
        建一条空记录**：空记录在飞书里就是个改不动的孤儿行，不如让用户先把表填上。
        """
        get_workbench(role)  # 校验角色合法
        payload = await self._prepare_write(fields)
        if not payload:
            raise CustomException(msg="新建失败：没有可写入的字段")

        data = await self._send(
            "POST",
            await self._endpoint("/records"),
            json={"fields": payload},
        )
        _check_lark_error(data, "新建记录")
        record = (data.get("data") or {}).get("record") or {}
        record_id = str(record.get("record_id") or "")
        if not record_id:
            # 飞书建成功了却没回 record_id，后续没法定位这条记录（也就没法改/删），
            # 当成失败报出来，免得界面刷新后凭空多一行且无人知道它是什么
            raise CustomException(msg="新建记录失败：飞书未返回记录 ID")
        log.info(f"飞书新建记录成功 {record_id}")
        # 新建的这一条不在缓存的索引里（键是 record_id），不清的话它在列表上会一直算成
        # 「没订单」那一档——与 `_batch_update` 同一个理由
        await self._invalidate_order_status(self._table_id)
        await self._bump_list_version()
        return {"record_id": record_id, "written": len(payload)}

    async def _group_peer_ids(self, record_id: str) -> list[str]:
        """取某条记录所属合并组的全部成员 record_id（含自身）。"""
        own = await self._link_ids_of([record_id], MERGE_MASTER_FIELD)
        hawb = await self._link_ids_of([record_id], MERGE_HAWB_FIELD)
        ids = set(own.get(record_id) or []) | set(hawb.get(record_id) or [])
        ids.add(record_id)
        return sorted(ids)

    async def merge_records(
        self,
        record_ids: list[str],
        merge_type: MergeType,
        target_no: str | None = None,
    ) -> dict[str, Any]:
        """把若干票并入同一合并组。

        §4.8 跨组重组：选中的票若已在别的合并组里，必须**先从原组摘出**再写新组。
        只写新组而不动旧组的话，旧组剩下的票仍引用被摘走的票，并查集会把两个组粘成
        一个（场景3 里 ABC + DEF 点 AD 会变成 ABCDEF 一组，怎么拆都拆不开）。
        故先按旧组把「剩余成员」重写掉，再写新组；两批写入分开提交，避免同一 record_id
        在一次 ``batch_update`` 里出现两条。

        关联字段写的是**全组成员的 record_id 数组（含自身）**——与飞书表既有数据格式一致；
        若给了 ``target_no``，同时写「合并主单」/「合并分单」单选字段。
        """
        if len(record_ids) < 2:
            raise CustomException(msg="合并失败：至少需要选择两条记录")
        ids = list(dict.fromkeys(record_ids))
        selected = set(ids)
        link_field = MERGE_MASTER_FIELD if merge_type == "master_awb" else MERGE_HAWB_FIELD
        select_field = "合并主单" if merge_type == "master_awb" else "合并分单"

        # 1) 先摘旧组：只重写剩余成员，选中的票紧接着会被新组覆盖，这里不写
        detach = _rebuild_updates(
            await self._expand_merge_groups(ids, link_field),
            link_field,
            selected,
            clear_removed=False,
        )
        detached = await self._batch_update(detach, "拆分原合并组")

        # 2) 再写新组
        updates: list[dict[str, Any]] = []
        for rid in ids:
            body: dict[str, Any] = {link_field: list(ids)}
            if target_no:
                body[select_field] = target_no
            updates.append({"record_id": rid, "fields": body})
        written = await self._batch_update(updates, "合并记录")
        await self._bump_list_version()
        log.info(
            f"飞书合并成功: {merge_type} 组内 {len(ids)} 票 -> {link_field}"
            f"（原组摘出 {detached} 条）"
        )
        return {
            "merged_count": written,
            "detached_count": detached,
            "record_ids": ids,
            "merge_type": merge_type,
        }

    async def unmerge_records(self, record_ids: list[str], merge_type: MergeType) -> dict[str, Any]:
        """把若干票从合并组中拆出。

        先按关联字段还原出完整的合并组（并查集），再把每组重写为「剔除被拆票之后的
        成员」：剩余 ≥2 票继续成组，只剩 1 票则整组解散（§4.8 场景1/2）。

        主单组拆出时**分单一并解除**（§4.5.3 / §4.8 场景5）。这里必须对**主单组全体
        成员**重算分单组，而不是只清被拆那几票——只清被拆票的话，同组剩下的票分单
        仍然互相引用，分单组还粘着（AB 主+分同时合并、拆 A 主单后，B 的分单还是 [A,B]）。
        """
        if not record_ids:
            raise CustomException(msg="拆出失败：未指定记录")
        leaving = set(dict.fromkeys(record_ids))
        link_field = MERGE_MASTER_FIELD if merge_type == "master_awb" else MERGE_HAWB_FIELD

        groups = await self._expand_merge_groups(sorted(leaving), link_field)
        updates = _rebuild_updates(groups, link_field, leaving, clear_removed=True)
        remaining_total = sum(
            len(members - leaving) for members in groups.values() if len(members - leaving) > 1
        )
        written = await self._batch_update(updates, "拆出合并组")

        hawb_written = 0
        if merge_type == "master_awb":
            affected = sorted({rid for members in groups.values() for rid in members})
            hawb_groups = await self._expand_merge_groups(affected, MERGE_HAWB_FIELD)
            hawb_updates = _rebuild_updates(
                hawb_groups, MERGE_HAWB_FIELD, leaving, clear_removed=True
            )
            # 分单是另一个字段，必须单独提交：同一条记录在一次 batch_update 里出现两条
            # （一条写主单、一条写分单）会被飞书判重，整批写入失败
            hawb_written = await self._batch_update(hawb_updates, "解除分单合并")

        await self._bump_list_version()
        log.info(
            f"飞书拆出成功: {merge_type} 拆出 {len(leaving)} 票，剩余 {remaining_total} 票，"
            f"写回 {written} 条（解除分单 {hawb_written} 条）"
        )
        return {
            "unmerged_count": len(leaving),
            "remaining_count": remaining_total,
            "written_count": written,
            "merge_type": merge_type,
        }

    async def download_media(self, file_token: str, table_id: str = "") -> tuple[bytes, str, str]:
        """下载飞书附件，返回 ``(内容, 文件名, 内容类型)``。

        必须走后端代理：附件 URL 带浏览器直接访问会报 ``99991661 Missing access token``，
        只有带上 ``tenant_access_token`` 才取得到内容（§12.3）。

        这里**一次性读完**而不是流式转发：飞书出错时返回的是 JSON 错误体（HTTP 也可能是
        200），流式会把那段 JSON 当成文件内容直接吐给浏览器，用户拿到一个「下载成功但打不开」
        的坏文件；读完才能先判错、给出可读文案。

        ``extra={"bitablePerm": {...}}`` 是**实测必需**的：不带它飞书直接返回
        ``400`` 且响应体为空（两个不同 file_token 均复现，非 token 失效），带上才 200。
        这正是「单元测试全绿、真实调用必挂」的那类差异——本文件顶部注释里写的第 3 个坑。

        ``table_id`` 是**附件所挂在的那张表**，默认取本实例的工作台表，但关联表的附件必须
        显式传：飞书按 ``tableId`` 判权限，拿商机主表的 id 去下「对客方案」里的附件会直接
        ``403``（实测），而挂在关联表上的附件恰恰是本系统里最常见的形态——商机自己的 49 个
        字段里一个附件字段都没有。
        """
        if not file_token:
            raise CustomException(msg="下载失败：缺少 file_token")
        _app_id, _secret, base_url = _require_credentials()
        self._require_datasource()
        extra = json.dumps(
            {"bitablePerm": {"tableId": table_id or self._table_id, "rev": 0}}, separators=(",", ":")
        )
        url = f"{base_url}/drive/v1/medias/{file_token}/download"

        # 与 `_send` 同一套 token 自愈：这一路不走 `_http_json`（要的是字节不是 JSON），但
        # 「缓存里的 token 已失效」这一种失败照样会发生，而且**不换 token 就永远失败**。
        token = await self._token()
        for attempt in (1, 2):
            response = await _get_client().get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"extra": extra},
            )
            content_type = response.headers.get("content-type") or ""
            # 正常下载拿到的是文件字节——别把它当 JSON 解（20MB 的附件白解一遍）。
            # 失败时飞书回的是 JSON（HTTP 400 或 200 都可能），解析出 token 业务码才算陈旧。
            stale = not (response.status_code == status.HTTP_200_OK and "json" not in content_type) and (
                _is_token_error_response(response)
            )
            if not stale or attempt == 2:
                break
            log.warning("飞书 access token 已失效（下载附件），清缓存重换后重试一次")
            await _drop_tenant_access_token(self._redis)
            token = await self._token()

        if response.status_code != status.HTTP_200_OK:
            raise CustomException(
                msg=f"飞书附件下载失败: [{response.status_code}] {_lark_error_text(response)}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        content_type = response.headers.get("content-type") or "application/octet-stream"
        if "json" in content_type:  # HTTP 200 但内容是错误 JSON
            raise CustomException(
                msg=f"飞书附件下载失败: {_lark_error_text(response)}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return response.content, _filename_from_disposition(response.headers.get("content-disposition") or ""), content_type

    async def upload_media(self, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        """上传附件到飞书，返回可直接写进附件字段的 ``{file_token, name, size, type}``。

        ``parent_type=bitable_file`` + ``parent_node=<app_token>`` 是必须的：附件要先挂到
        该多维表格所在的 base 上，否则拿到的 file_token 写进字段会被飞书拒掉。
        """
        if not filename:
            raise CustomException(msg="上传失败：缺少文件名")
        if not content:
            raise CustomException(msg="上传失败：文件为空")
        if len(content) > _MAX_UPLOAD_BYTES:
            raise CustomException(
                msg=f"上传失败：文件 {len(content) / 1024 / 1024:.1f}MB 超过飞书 {_MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限",
            )
        _app_id, _secret, base_url = _require_credentials()
        self._require_datasource()
        data = await self._send(
            "POST",
            f"{base_url}/drive/v1/medias/upload_all",
            data={
                "file_name": filename,
                "parent_type": "bitable_file",
                "parent_node": self._app_token,
                "size": str(len(content)),
            },
            files={"file": (filename, content, content_type or "application/octet-stream")},
        )
        _check_lark_error(data, "上传附件")
        file_token = (data.get("data") or {}).get("file_token")
        if not file_token:
            raise CustomException(
                msg="飞书上传附件失败：未返回 file_token",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        log.info(f"飞书附件上传成功: {filename}（{len(content)} 字节）")
        return {
            "file_token": str(file_token),
            "name": filename,
            "size": len(content),
            "type": content_type or "application/octet-stream",
        }

    async def count_todo_breakdown(self, role: str, current_user: Any | None = None) -> list[dict[str, Any]]:
        """逐勾选框的待办/已完成计数（前端渲染为可点击的分项卡片）。

        每一项都是独立计数而非并集的一部分——本表多数勾选框是完成标记，未勾选的行接近
        全表，并集没有区分度（实测客服两项并集 1958/1990），分开看才有意义。

        ``current_user`` 有值时同样按 ``cfg.owner_field`` 过滤：卡片数字若不同步过滤，
        就会出现「卡片写 1958 条、点进去只有 3 条」这种自相矛盾的界面。
        """
        cfg = get_workbench(role)
        owner_cond = await self._owner_filter(current_user)

        async def one(cb: str) -> dict[str, Any]:
            async def count(condition: dict[str, Any]) -> int:
                data = await self._search_page(_combine_filter(owner_cond, condition), None, 1)
                return int(data.get("total") or 0)

            pending, done = await asyncio.gather(
                count(_is_false_condition(cb)),
                count(_is_true_condition(cb)),
            )
            return {"field": cb, "pending": pending, "done": done}

        return list(await asyncio.gather(*(one(cb) for cb in cfg.todo_checkboxes)))

    async def count_total(self, current_user: Any | None = None) -> int:
        """全部记录数（供工作台卡片展示）。``current_user`` 有值时按 ``cfg.owner_field`` 过滤。"""
        data = await self._search_page(_combine_filter(await self._owner_filter(current_user)), None, 1)
        return int(data.get("total") or 0)


# 供其他模块/测试引用的出口
__all__ = [
    "FeishuBitableService",
    "denormalize_field_value",
    "normalize_field_value",
]
