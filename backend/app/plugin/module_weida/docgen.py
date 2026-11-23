"""单据生成：调用随包 Node 渲染器（vendor/docgen/run.js），主单/分单/舱单/booking/到货预报表。

渲染器来自「威达单据生成包」（妙搭 docs 模块打包版），输入是飞书口径的中文键名，
所以这里把本地行按 `sys_field_label` 反译成中文键，再交给 Node。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.database import async_db_session

NODE = os.getenv("DOCGEN_NODE", "/home/ubuntu/weida/node/bin/node")
RENDERER = Path(__file__).resolve().parents[3] / "vendor" / "docgen" / "run.js"

DOC_KINDS = (
    "mawb",
    "mawbVerify",
    "hawb",
    "hawbVerify",
    "hawbRandom",
    "manifest",
    "manifestRandom",
    "booking",
    "arrival",
)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%dT00:00:00+08:00")
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


async def _labels(session: Any, table: str) -> dict[str, str]:
    rows = (
        await session.execute(
            text("SELECT col, label FROM sys_field_label WHERE tbl = :t"), {"t": table}
        )
    ).all()
    return {str(col): str(label) for col, label in rows}


def _to_record(row: Any, labels: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in dict(row).items():
        if value is None:
            continue
        record[labels.get(key, key)] = _plain(value)
    return record


async def _build_input(order_id: int) -> dict[str, Any]:
    async with async_db_session() as session:
        order = (
            await session.execute(
                text("SELECT * FROM biz_order WHERE id = :id AND is_deleted = 0"),
                {"id": order_id},
            )
        ).mappings().first()
        if not order:
            raise ValueError("订单不存在")
        ifreights = (
            await session.execute(
                text(
                    "SELECT i.* FROM biz_link l JOIN biz_ifreight i ON i.id = l.to_id "
                    "AND i.is_deleted = 0 WHERE l.from_table = 'order' "
                    "AND l.to_table = 'ifreight' AND l.field = '所属订单' "
                    "AND l.from_id = :id ORDER BY i.id"
                ),
                {"id": order_id},
            )
        ).mappings().all()
        vendor = None
        code = order.get("foreign_agent_code") or order.get("foreign_agent_name")
        if code:
            vendor = (
                await session.execute(
                    text(
                        "SELECT * FROM biz_vendor WHERE is_deleted = 0 AND ("
                        " merchant_no = :c OR merchant_full_name = :c "
                        " OR merchant_short_name = :c) ORDER BY id LIMIT 1"
                    ),
                    {"c": code},
                )
            ).mappings().first()
        order_labels = await _labels(session, "biz_order")
        ifr_labels = await _labels(session, "biz_ifreight")
        vendor_labels = await _labels(session, "biz_vendor")

    return {
        "order": {"id": str(order_id), "record": _to_record(order, order_labels)},
        "ifreights": [
            {"id": str(row["id"]), "record": _to_record(row, ifr_labels)}
            for row in ifreights
        ],
        "vendor": (
            {"id": str(vendor["id"]), "record": _to_record(vendor, vendor_labels)}
            if vendor
            else None
        ),
    }


async def render_documents(
    order_id: int, kinds: list[str]
) -> tuple[bytes, str, str]:
    """生成单据，返回 (内容, 文件名, mime)。多份时打成一个 zip。"""
    wanted = [k for k in kinds if k in DOC_KINDS] or list(DOC_KINDS)
    if not RENDERER.exists():
        raise ValueError("单据渲染器未安装")
    node = NODE if Path(NODE).exists() else shutil.which("node") or ""
    if not node:
        raise ValueError("服务器上没有可用的 Node 运行环境")
    payload = await _build_input(order_id)
    workdir = Path(tempfile.mkdtemp(prefix="docgen-"))
    try:
        source = workdir / "input.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        outdir = workdir / "out"
        outdir.mkdir()
        proc = await asyncio.create_subprocess_exec(
            node,
            str(RENDERER),
            str(source),
            str(outdir),
            f"--kind={','.join(wanted)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError(
                "单据生成失败：" + (stderr.decode("utf-8", "ignore").strip()[-400:] or "未知错误")
            )
        files = sorted(p for p in outdir.iterdir() if p.is_file())
        if not files:
            raise ValueError("单据生成失败：没有产出文件")
        if len(files) == 1:
            data = files[0].read_bytes()
            return data, files[0].name, _mime(files[0].name)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, path.name)
        return buf.getvalue(), f"单据-{order_id}.zip", "application/zip"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _mime(name: str) -> str:
    if name.lower().endswith(".pdf"):
        return "application/pdf"
    if name.lower().endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"
