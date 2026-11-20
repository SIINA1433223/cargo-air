"""飞书 token 自愈（`FeishuBitableService._send`）。

缓存里的 `tenant_access_token` 可能提前失效（应用被改动授权、飞书侧作废），此时**所有**
飞书接口一起报错：HTTP 层的 400 与业务层的 200+非 0 code 都可能是它。这两种都长得像
「参数写错了」，照着参数去查会查很久（实测踩过两次，见备忘），所以：

- 认出 token 类报错 → 清缓存 + 重换 + **原样再打一次**（只重试一次）；
- 认不出的失败 → 一次都不重试，原样抛出去（把参数错也重试只会把一次失败拖成两次）。

桩打在模块级 `_http_json` 上：`_send` 的整个逻辑（判错、清缓存、重换、重试）都是真的，
只有「网上那一步」被换掉。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import httpx
import pytest
from redis.asyncio.client import Redis

from app.plugin.module_feishu import service as service_module
from app.plugin.module_feishu.service import FeishuBitableService

ROLE = "wd_domestic_ops"
URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/x/tables/y/fields"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _http_error(code: int) -> httpx.HTTPStatusError:
    """飞书那种 400 + body 里带业务码的失败。"""
    request = httpx.Request("GET", URL)
    response = httpx.Response(400, json={"code": code, "msg": "boom"}, request=request)
    return httpx.HTTPStatusError("400 Bad Request", request=request, response=response)


class FakeToken(FeishuBitableService):
    """按顺序发 token：第一次给旧的、第二次给新的（`_drop_tenant_access_token` 之后）。"""

    def __init__(self, tokens: list[str]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.tokens = list(tokens)
        self.minted: list[str] = []
        self.dropped = 0

    async def _token(self) -> str:
        token = self.tokens.pop(0) if len(self.tokens) > 1 else self.tokens[0]
        self.minted.append(token)
        return token


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """调用流水：`http:旧` 表示拿旧 token 打了一次，`drop` 表示清了缓存。"""
    log: list[str] = []

    async def drop(redis: Any) -> None:
        log.append("drop")

    monkeypatch.setattr(service_module, "_drop_tenant_access_token", drop)
    return log


def _install(monkeypatch: pytest.MonkeyPatch, log: list[str], behaviour: list[Any]) -> None:
    """`behaviour` 里每一项：要么是异常（抛），要么是返回值（回）。"""

    async def fake_http(method: str, url: str, token: str, **kwargs: Any) -> dict:
        log.append(f"http:{token}")
        result = behaviour.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(service_module, "_http_json", fake_http)


def test_http_400_说_token_失效就清缓存重换再打一次(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    svc = FakeToken(["旧", "新"])
    _install(monkeypatch, events, [_http_error(99991663), {"code": 0, "data": {"ok": True}}])

    data = run(svc._send("GET", URL, params={"page_size": 100}))

    assert data == {"code": 0, "data": {"ok": True}}
    assert events == ["http:旧", "drop", "http:新"]  # 第二遍换的是新 token
    assert svc.minted == ["旧", "新"]


def test_清缓存这一步真的发生了(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """只清缓存不重试＝用户得自己再点一下；只重试不清缓存＝两遍用的是同一个废 token。
    两件事都要做，所以这里钉的是「重试之前先清」。"""
    svc = FakeToken(["旧", "新"])
    _install(monkeypatch, events, [_http_error(99991663), {"code": 0}])

    run(svc._send("GET", URL))

    assert events == ["http:旧", "drop", "http:新"]  # 先 drop，再旧、再新


def test_业务层的_token_失效_cod_也认(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """飞书有的接口回 200 + 非 0 code：只盯 HTTP 状态码会漏掉这一半。"""
    svc = FakeToken(["旧", "新"])
    _install(monkeypatch, events, [{"code": 99991663, "msg": "Invalid access token"}, {"code": 0}])

    assert run(svc._send("GET", URL)) == {"code": 0}
    assert events == ["http:旧", "drop", "http:新"]


def test_别的业务码一次都不重试(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """参数写错、字段不存在这类 400：重试没有意义，只会把一次失败拖成两次。"""
    svc = FakeToken(["旧"])
    _install(monkeypatch, events, [_http_error(1254005)])

    with pytest.raises(httpx.HTTPStatusError):
        run(svc._send("GET", URL))

    assert events == ["http:旧"]


def test_别的业务码_200_也一次都不重试(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    svc = FakeToken(["旧"])
    _install(monkeypatch, events, [{"code": 1254005, "msg": "FieldNameNotFound"}])

    # 200 的业务错由调用方的 `_check_lark_error` 报，`_send` 只负责原样交出去
    assert run(svc._send("GET", URL)) == {"code": 1254005, "msg": "FieldNameNotFound"}
    assert events == ["http:旧"]


def test_重换还是失效就不再试第三次(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """第二遍仍失败说明换 token 救不了（密钥错、应用被停用），原样冒泡让人看见真报错。"""
    svc = FakeToken(["旧", "新"])
    _install(monkeypatch, events, [_http_error(99991663), _http_error(99991663)])

    with pytest.raises(httpx.HTTPStatusError):
        run(svc._send("GET", URL))

    assert events == ["http:旧", "drop", "http:新"]


def test_顺利的时候只打一次(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    svc = FakeToken(["旧"])
    _install(monkeypatch, events, [{"code": 0}])

    assert run(svc._send("GET", URL)) == {"code": 0}
    assert events == ["http:旧"]
