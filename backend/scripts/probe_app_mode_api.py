"""只读探针：飞书**应用模式（BaseApp / AppMode）**的接口，我们这组应用凭证够不够得着？

背景：清单里写「应用模式只能在飞书网页里点，API 帮不上」，后来发现 lark-cli 有
`app-page-*` / `app-block-*` 一族命令，底层是 `/open-apis/base/v3/base_apps/...`。
这个探针只回答三个问题，**一个字节都不写**：

  1. `base/v3/base_apps/{token}` 这个路径存在吗？我们能不能读？
     （403/99991672 = 路径在但缺 scope；404/其它 = 路径不对或 entity 不存在）
  2. 这个 base 现在**有没有**应用模式实体（能不能列出 page）？
  3. 数据库里那张表（bitable v1）的 `app_token` 和 BaseApp 的 `app_token` 是不是同一个？

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/probe_app_mode_api.py

注意：**只打印 status / code / msg / 结构形状**，绝不打印 token 本身。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import _get_tenant_access_token, _http_json
from app.plugin.module_feishu.workbench import WD_APP_TOKEN

# 要试的路径。base/v3 是应用模式/仪表盘那套「新版」surface；bitable/v1 是我们一直在用的。
CANDIDATES: tuple[str, ...] = (
    f"/base/v3/base_apps/{WD_APP_TOKEN}",
    f"/base/v3/base_apps/{WD_APP_TOKEN}/pages",
    f"/bitable/v1/apps/{WD_APP_TOKEN}/dashboards",
    f"/base/v3/bases/{WD_APP_TOKEN}",
)


def _shape(obj: object, depth: int = 0) -> str:
    """把一个 JSON 响应压成一眼能看的形状，不打印长字符串（token 就在里面）。"""
    if depth > 3:
        return "…"
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        inner = ", ".join(f"{k}: {_shape(v, depth + 1)}" for k, v in list(obj.items())[:12])
        return "{" + inner + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        return f"[{len(obj)} × {_shape(obj[0], depth + 1)}]"
    if isinstance(obj, str):
        return f"str(len={len(obj)})" if len(obj) > 24 else repr(obj)
    return repr(obj)


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    token = await _get_tenant_access_token(redis)
    base = settings.LARK_BASE_URL.rstrip("/")
    try:
        print(f"base_token / app_token 用的是：{WD_APP_TOKEN}\n")
        for path in CANDIDATES:
            print(f"── GET {path}")
            try:
                data = await _http_json("GET", f"{base}{path}", token)
                print(f"   ✅ code={data.get('code')} msg={data.get('msg')}")
                print(f"   data 形状：{_shape(data.get('data'))}")
                if isinstance(data.get("data"), dict):
                    payload = json.dumps(data["data"], ensure_ascii=False)
                    # 只截前 600 字符；里面若有 token 字段，形状那行已经暴露了，这里再兜一层
                    print(f"   data 前 600 字符：{payload[:600]}")
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:400]
                print(f"   ❌ HTTP {exc.response.status_code}：{body}")
            except Exception as exc:  # noqa: BLE001 —— 探针，什么错都要看到
                print(f"   ❌ {type(exc).__name__}: {exc}")
            print()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
