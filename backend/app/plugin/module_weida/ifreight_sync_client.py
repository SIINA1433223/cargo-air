"""iFreight SaveAirBooking：按 Java 引擎（wfs）的契约签名并 POST，回写同步状态。

凭证放在 `/home/ubuntu/weida/.secrets/ifreight.env`（IFREIGHT_URL / APP_KEY / APP_SECRET），
不打印、不写日志。选项编码（揽货方式/业务类型/部门/航线/单据类型的 iFreight 编码）
运行时从飞书「国内操作选项设置」表读取，缓存 10 分钟。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

from . import feishu_sync as fs

ENV_FILE = Path("/home/ubuntu/weida/.secrets/ifreight.env")
GROUPS = {"GroupId": "WFS", "CMP": "WD", "STN": "TAO"}
GW_UNIT = "K"
DEFAULT_NOTY_CODE = "SAC"
DEFAULT_CNEE_CODE = "UFOO"
OPTION_TABLE = "tblFf1Zce9K801K6"  # 国内操作选项设置

_option_cache: tuple[float, dict[str, dict[str, str]]] | None = None


def _env() -> dict[str, str]:
    data: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def sign(app_secret: str, timestamp: str, nonce: str, body: str) -> str:
    secret = hashlib.md5(app_secret.encode()).hexdigest()
    return hashlib.md5((secret + timestamp + nonce + body + secret).encode()).hexdigest()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in value
        ).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "").strip()
    return str(value).strip()


def _option_maps() -> dict[str, dict[str, str]]:
    """中文选项 → iFreight 编码（单据类型/揽货方式/业务类型/部门/航线）。"""
    global _option_cache
    now = time.time()
    if _option_cache and now - _option_cache[0] < 600:
        return _option_cache[1]
    env = fs.load_env()
    token = fs.token(env)
    items = fs.records(token, fs.MAIN, OPTION_TABLE)
    pairs = (
        ("单据类型", "单据类型-iFreight编码"),
        ("单据类型", "单据类型-iFreight 编码"),
        ("揽货方式", "揽货方式-iFreight 编码"),
        ("揽货方式", "揽货方式-iFreight编码"),
        ("业务类型", "业务类型-iFreight 编码"),
        ("业务类型", "业务类型-iFreight 编码"),
        ("业务所属部门", "业务所属部门-iFreight编码"),
        ("业务所属部门", "业务所属部门-iFreight 编码"),
        ("航线", "航线-iFreight 编码"),
        ("航线", "航线-iFreight编码"),
    )
    maps: dict[str, dict[str, str]] = {}
    for item in items:
        fields = item.get("fields", {})
        for value_key, code_key in pairs:
            value = _text(fields.get(value_key))
            code = _text(fields.get(code_key))
            if value and code:
                maps.setdefault(value_key, {})[value] = code
    _option_cache = (now, maps)
    return maps


def _code(maps: dict[str, dict[str, str]], option: str, value: Any) -> str:
    """中文选项 → iFreight 编码；对不上就原样传（iFreight 会给出明确报错）。"""
    raw = _text(value)
    if not raw:
        return ""
    return maps.get(option, {}).get(raw, raw)


async def _load_order(ifreight_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM biz_ifreight WHERE id = :id AND is_deleted = 0"),
                {"id": ifreight_id},
            )
        ).mappings().first()
        if not row:
            raise ValueError("iFreight 记录不存在")
        order = (
            await session.execute(
                text(
                    "SELECT o.* FROM biz_link l JOIN biz_order o ON o.id = l.from_id "
                    "AND o.is_deleted = 0 WHERE l.from_table = 'order' "
                    "AND l.to_table = 'ifreight' AND l.field = '所属订单' "
                    "AND l.to_id = :id LIMIT 1"
                ),
                {"id": ifreight_id},
            )
        ).mappings().first()
    if not order:
        raise ValueError("这条 iFreight 没有挂订单")
    return {"order": dict(order), "row": dict(row)}


def build_body(order: dict[str, Any], row: dict[str, Any], maps: dict[str, dict[str, str]]) -> dict[str, Any]:
    booking_no = _text(row.get("customer_ref_no"))
    if not booking_no:
        raise ValueError("iFreight 行必须有客户 REF NO.")
    operator = _text(row.get("domestic_operation")) or _text(order.get("domestic_operation"))
    if not operator:
        raise ValueError("必须有国内操作（Operator 代码）")
    body: dict[str, Any] = {
        **GROUPS,
        "Operator": operator,
        "BookingNo": booking_no,
        "Nomination": _code(maps, "揽货方式", row.get("cargo_receiving_method")),
        "BusinessType": _code(maps, "业务类型", row.get("business_type")),
        "SalesDep": _code(maps, "业务所属部门", row.get("business_department")),
        "AirRouteCode": _code(maps, "航线", row.get("route")),
        "BlType": _code(maps, "单据类型", row.get("document_type")),
    }
    etd = row.get("estimated_departure_time") or order.get("flight")
    if isinstance(etd, datetime):
        etd_text = etd.strftime("%Y-%m-%dT00:00:00")
        for key in ("ETD", "ETA", "InvDate", "BlDate"):
            body[key] = etd_text
    def put(key: str, value: Any) -> None:
        text_value = _text(value)
        if text_value:
            body[key] = text_value

    put("PrcpName", row.get("client_name"))
    put("PrcpAddr", row.get("client_address"))
    put("BkgAgtName", row.get("booking_agent") or row.get("booking_agent_name"))
    put("ShprName", row.get("shipper_name"))
    put("ShprAddr", row.get("shipper_address"))
    put("CneeName", row.get("consignee_name"))
    put("CneeAddr", row.get("consignee_address"))
    body["CneeCode"] = DEFAULT_CNEE_CODE
    body["NotyCode"] = _text(row.get("notify_party_code")) or DEFAULT_NOTY_CODE
    put("NotyName", row.get("notify_party_name"))
    put("NotyAddr", row.get("notify_party_address"))
    put("AgtName", row.get("foreign_agent"))
    put("POLCityCode", _text(row.get("origin_city_code")).upper() or None)
    put("PODCityCode", _text(row.get("dest_city_code")).upper() or None)
    put("MBlNo", order.get("master_no"))
    put("FreightPrice", row.get("freight_payment_method"))
    put("OtherPrice", row.get("surcharge_payment_method"))
    for key, column in (("Pkg", "quantity"), ("Gw", "gross_weight"), ("Cbm", "volume"),
                        ("ChargeableWeight", "customer_billing_weight"),
                        ("VolumetricWeight", "volume_weight")):
        value = row.get(column)
        if value is not None and value != "":
            body[key] = float(value) if hasattr(value, "__float__") else value
    if "Gw" in body:
        body["GwUnit"] = GW_UNIT
    put("PkgUnit", row.get("piece_unit"))
    put("PPCurrencyCode", row.get("pp_currency"))
    put("FlightNo", row.get("flight_number"))
    put("Incoterm", row.get("transport_clause"))
    put("AirlineCode", _text(row.get("airline_company"))[:2] or None)
    put("Descp", row.get("goods_description"))
    put("GoodsSize", row.get("dimension"))
    put("Marks", row.get("shipping_mark"))
    put("Remark", row.get("remark"))
    return body


def _post(url: str, app_key: str, app_secret: str, body_text: str) -> dict[str, Any]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    request = urllib.request.Request(
        url,
        data=body_text.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "key": app_key,
            "timestamp": timestamp,
            "nonce": nonce,
            "sign": sign(app_secret, timestamp, nonce, body_text),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _success(payload: dict[str, Any]) -> bool:
    for key in ("Successful", "successful", "success", "Success", "isSuccess", "IsSuccess"):
        if key in payload:
            value = payload[key]
            return value is True or str(value).strip().lower() in ("true", "1")
    return False


async def sync_ifreight(ifreight_id: int) -> dict[str, Any]:
    env = _env()
    if not env.get("IFREIGHT_URL") or not env.get("IFREIGHT_APP_KEY"):
        raise ValueError("iFreight 凭证未配置")
    data = await _load_order(ifreight_id)
    maps = _option_maps()
    body = build_body(data["order"], data["row"], maps)
    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    status, reason, bl_no, request_json = "成功", "", "", body_text
    try:
        payload = _post(
            env["IFREIGHT_URL"], env["IFREIGHT_APP_KEY"], env["IFREIGHT_APP_SECRET"], body_text
        )
        if not _success(payload):
            status = "失败"
            reason = str(
                payload.get("message") or payload.get("Message") or payload.get("msg") or "iFreight 返回失败"
            )[:500]
        else:
            data_node = payload.get("Data") or payload.get("data") or {}
            if isinstance(data_node, dict):
                bl_no = _text(data_node.get("BlNo") or data_node.get("blNo") or data_node.get("BLNo"))
    except Exception as exc:  # 网络/HTTP/解析失败都回写失败原因
        status, reason = "失败", f"{type(exc).__name__}: {exc}"[:500]
    async with async_db_session() as session:
        await session.execute(
            text(
                "UPDATE biz_ifreight SET sync_status = :s, sync_time = NOW(3), "
                "fail_reason = :r, request_json = :j, house_bl_no = COALESCE(NULLIF(:bl,''), house_bl_no), "
                "update_time = NOW(3) WHERE id = :id"
            ),
            {"s": status, "r": reason, "j": request_json[:100000], "bl": bl_no, "id": ifreight_id},
        )
        await session.commit()
    return {"status": status, "reason": reason, "bl_no": bl_no, "booking_no": body["BookingNo"]}
