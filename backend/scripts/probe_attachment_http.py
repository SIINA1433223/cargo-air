"""端到端探针：走**真实的 HTTP 路由**下载附件（只读，不写任何东西）。

为什么不止步于服务层：本轮改的是 controller 的查询参数（role / table_id），
服务层测试与 pyright 都看不见「参数有没有真的从 URL 传到 service」。这里用 TestClient
建真 app、真 Redis、真飞书凭证，只把登录态换成超级管理员，其余全是生产路径。

用法：
    PYTHONPATH=$PWD .venv/bin/python scripts/probe_attachment_http.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import redis.asyncio as aioredis
from fastapi.testclient import TestClient

from app.config.setting import settings
from app.core.auth.schema import AuthSchema
from app.core.dependencies import get_current_user
from app.plugin.module_feishu import service as service_module
from app.plugin.module_feishu.service import FeishuBitableService
from main import create_app

ROLE = "wd_customer_service"
DUIFANG_TABLE = "tblcTmqLBPpDxbIb"
"""对客方案表：商机自己 49 个字段里没有任何附件字段，附件全在这类关联表上。"""


class _SuperUser:
    """只要 `is_superuser` —— `AuthPermission.__call__` 见它就直接放行。"""

    is_superuser = True


def fake_auth() -> AuthSchema:
    """绕开登录态。

    `model_construct` 而不是构造器：`db` 是必填字段且类型就是 AsyncSession，
    这里根本用不到它，硬塞一个假对象过不了校验。
    """
    return AuthSchema.model_construct(user=_SuperUser(), check_data_scope=False, db=None)


async def pick_token() -> str:
    """从「对客方案」表里挑一个真实附件（只读）。"""
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        svc = FeishuBitableService(redis, ROLE)
        raw = await svc._list_raw_records(DUIFANG_TABLE, limit=50)
        for rec in raw:
            for value in (rec.get("fields") or {}).values():
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, dict) and item.get("file_token") and item.get("name"):
                        print(f"选中附件：{item['name']}")
                        return str(item["file_token"])
    finally:
        await redis.aclose()
    raise SystemExit("对客方案表里没找到附件，无法验证")


def main() -> int:
    token = asyncio.run(pick_token())
    # 上面那次 asyncio.run 关掉了自己的事件循环，而模块级共享的 httpx 客户端是在那个
    # 循环里建的连接池。不清掉的话，下面 TestClient（自己的循环）复用到的是个死客户端。
    service_module._client = None

    app = create_app()
    # 只替换登录态：超级管理员直接过权限校验（见 AuthPermission.__call__），
    # 其余（Redis、飞书 token、HTTP 下载）全走真实路径。
    app.dependency_overrides[get_current_user] = fake_auth

    with TestClient(app) as client:
        url = "/feishu/records/file/download"
        cases: list[tuple[str, dict[str, Any]]] = [
            ("① 不带 role/table_id（改动前的行为）", {"file_token": token}),
            ("② 只带 role", {"file_token": token, "role": ROLE}),
            ("③ role + 附件所在表", {"file_token": token, "role": ROLE, "table_id": DUIFANG_TABLE}),
        ]
        for tag, params in cases:
            resp = client.get(url, params=params)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "json" not in ctype:
                print(f"  {tag} → ✅ 200, {len(resp.content)} 字节, {ctype}")
            else:
                body = resp.text[:140].replace("\n", " ")
                print(f"  {tag} → ❌ {resp.status_code} {ctype} {body}")

        # 权限：不带登录态必须还是被拦，别为了改下载把门打开
        app.dependency_overrides.pop(get_current_user)
        resp = client.get(url, params={"file_token": token, "role": ROLE, "table_id": DUIFANG_TABLE})
        print(f"  ④ 无登录态 → {resp.status_code}（期望 401/403）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
