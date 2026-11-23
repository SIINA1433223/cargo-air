"""把飞书上的附件字段下载到本地（biz_attachment），供 Fast 界面下载/预览。

幂等：同一 (owner_table, owner_id, field, file_name) 已存在就跳过。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/sync_attachments.py            # 只看
    PYTHONPATH=$PWD .venv/bin/python scripts/sync_attachments.py --apply    # 真下载
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from sqlalchemy import text

from app.core.database import async_db_session
from app.plugin.module_weida import feishu_sync as fs

STORE = Path("/home/ubuntu/weida/uploads/feishu")
# 本地表 → biz_attachment.owner_table 键
OWNER_KEYS = {
    "biz_order": "order",
    "biz_ifreight": "ifreight",
    "biz_plan": "plan",
    "biz_pricing": "pricing",
    "biz_opportunity": "opportunity",
    "biz_vendor": "vendor",
}
TARGETS = ["biz_order", "biz_ifreight", "biz_plan", "biz_pricing"]


def _download(token: str, table_id: str, file_token: str) -> bytes:
    extra = base64.b64encode(
        json.dumps({"bitablePerm": {"tableId": table_id}}).encode()
    ).decode()
    url = (
        f"{fs.API}/drive/v1/medias/{urllib.parse.quote(file_token)}/download"
        f"?extra={urllib.parse.quote(extra)}"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=120) as resp:
        return resp.read()


def _attachments(fields: dict) -> list[tuple[str, str, dict]]:
    """[(字段名, file_token, 元信息)]"""
    out = []
    for name, value in fields.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("file_token"):
                out.append((name, str(item["file_token"]), item))
    return out


async def run(apply: bool) -> None:
    env = fs.load_env()
    token = fs.token(env)
    total = skipped = 0
    for _app, table_id, name, local in fs.TABLES:
        if local not in TARGETS:
            continue
        owner = OWNER_KEYS[local]
        items = fs.records(token, fs.MAIN, table_id)
        async with async_db_session() as session:
            id_map = {
                str(row[0]): int(row[1])
                for row in (
                    await session.execute(
                        text(f"SELECT feishu_record_id, id FROM `{local}` WHERE feishu_record_id IS NOT NULL")
                    )
                ).all()
            }
            for item in items:
                local_id = id_map.get(str(item.get("record_id") or ""))
                if not local_id:
                    continue
                for field, file_token, meta in _attachments(item.get("fields", {})):
                    file_name = urllib.parse.unquote(str(meta.get("name") or file_token))
                    exists = (
                        await session.execute(
                            text(
                                "SELECT 1 FROM biz_attachment WHERE owner_table=:t AND owner_id=:o "
                                "AND field=:f AND file_name=:n LIMIT 1"
                            ),
                            {"t": owner, "o": local_id, "f": field, "n": file_name},
                        )
                    ).first()
                    if exists:
                        skipped += 1
                        continue
                    total += 1
                    print(f"  {name} #{local_id} {field} ← {file_name}")
                    if not apply:
                        continue
                    try:
                        data = _download(token, table_id, file_token)
                    except Exception as exc:  # 单个失败不影响整体
                        print(f"    下载失败：{type(exc).__name__} {exc}"[:200])
                        continue
                    target_dir = STORE / owner / str(local_id)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = file_name.replace("/", "_")
                    path = target_dir / safe_name
                    path.write_bytes(data)
                    mime = str(meta.get("type") or "application/octet-stream")
                    await session.execute(
                        text(
                            "INSERT INTO biz_attachment (owner_table, owner_id, field, file_name, "
                            "file_size, mime, storage_key) VALUES (:t,:o,:f,:n,:s,:m,:k)"
                        ),
                        {
                            "t": owner,
                            "o": local_id,
                            "f": field,
                            "n": file_name,
                            "s": len(data),
                            "m": mime,
                            "k": str(path),
                        },
                    )
            if apply:
                await session.commit()
    print(f"待下载 {total}，已存在 {skipped}" + ("" if apply else "（只读预演，加 --apply 才下载）"))


if __name__ == "__main__":
    import asyncio

    asyncio.run(run("--apply" in sys.argv))
