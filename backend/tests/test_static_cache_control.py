"""静态资源的 `Cache-Control`：入口每次复验，带 hash 的产物永久缓存。

这两条是「以后自己改版不会卡住同事」的全部依据，而它们**坏掉的方式都是静默的**：

- 入口若被永久缓存 → 发新版后同事仍用缓存的旧 `index.html`，而它引用的 chunk 已经
  随 `dist/` 替换下线 → **白屏，且刷新救不回来**（清站点数据才行）。
- 产物若被当成「每次复验」→ 只是慢一点，不致命；但**反过来**把「人手起的名字」误判成
  带 hash 而永久缓存，就和上面那条同样致命（改了内容名字没变，同事永远拿旧的）。

所以下面两个方向都要钉：该 immutable 的必须 immutable，**不该的必须 no-cache**。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.scripts.init_app import _IMMUTABLE_CACHE, _REVALIDATE_CACHE, _is_hashed_asset, _StaticFiles

# 真实产物里的样子（随便挑几个现成的名字）
HASHED = [
    "index.BOHpjF4n.js",
    ".pnpm.C2wJ2VeW.js",
    "element-plus.BeRoj5YW.css",
    "bg.DrCBEYh-.webp",
    "401.BdWuHcJA.svg",
    "inter.9f3a2b1c.woff2",
]

# 没有 hash、**必须**每次复验的
NOT_HASHED = [
    "index.html",
    "favicon.ico",
    "favicon.svg",
    "background.svg",
    "manifest.webmanifest",
    "sw.js",
    # 两个点、看着像产物，其实是人手起的名字——误判成 immutable 就会卡住同事
    "my.component.js",
    "site.settings.css",
    "highlight.js",
    # hash 段太短，不足以认定是内容 hash
    "app.a1b2.js",
]


@pytest.mark.parametrize("name", HASHED)
def test_带内容hash的产物名被识别(name: str) -> None:
    assert _is_hashed_asset(name), f"{name} 是产物名，应判为可永久缓存"


@pytest.mark.parametrize("name", NOT_HASHED)
def test_人手起的名字不算hash(name: str) -> None:
    assert not _is_hashed_asset(name), (
        f"{name} 没有内容 hash，判成 immutable 会让同事永久卡在旧内容上"
    )


def _respond(tmp_path: Path, name: str, *, if_none_match: str | None = None) -> tuple[int, str]:
    """让 `_StaticFiles` 真走一次 `file_response`，返回 (状态码, Cache-Control)。"""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    # 已存在就别重写：重写会刷新 mtime，而 FileResponse 的 etag 是从 stat 算的，
    # 那样第二次调用的 etag 和第一次对不上，304 永远命不中（用例会假红）。
    if not target.exists():
        target.write_bytes(b"// content\n")

    static = _StaticFiles(tmp_path, "/web")
    scope: dict = {"type": "http", "headers": []}
    if if_none_match:
        scope["headers"] = [(b"if-none-match", if_none_match.encode())]

    response = static.file_response(target, os.stat(target), scope)
    return response.status_code, str(response.headers.get("cache-control", ""))


def test_入口html每次都要复验(tmp_path: Path) -> None:
    status, cache = _respond(tmp_path, "index.html")
    assert status == 200
    assert cache == _REVALIDATE_CACHE, "入口被缓存住 = 发版后同事白屏"


def test_带hash的js可以永久缓存(tmp_path: Path) -> None:
    _, cache = _respond(tmp_path, "js/index.BOHpjF4n.js")
    assert cache == _IMMUTABLE_CACHE, "产物名带 hash 却不给 immutable，等于每次白跑一趟网络"


def test_人手名字的js不能永久缓存(tmp_path: Path) -> None:
    _, cache = _respond(tmp_path, "js/my.component.js")
    assert cache == _REVALIDATE_CACHE


def test_复验命中304时缓存策略不能丢(tmp_path: Path) -> None:
    """304 若不带 `Cache-Control`，这一次的缓存策略就白设了。"""
    _, etag_response = _respond(tmp_path, "js/index.BOHpjF4n.js")
    # 先拿一次真实 etag：直接问那个 200 响应要
    target = tmp_path / "js/index.BOHpjF4n.js"
    static = _StaticFiles(tmp_path, "/web")
    etag = str(static.file_response(target, os.stat(target), {"type": "http", "headers": []}).headers["etag"])

    status, cache = _respond(tmp_path, "js/index.BOHpjF4n.js", if_none_match=etag)
    assert status == 304
    assert cache == _IMMUTABLE_CACHE, f"304 丢了缓存策略（拿到的是 {cache!r}）"


def test_入口304也保持no_cache(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    target.write_bytes(b"<html></html>")
    static = _StaticFiles(tmp_path, "/web")
    etag = str(static.file_response(target, os.stat(target), {"type": "http", "headers": []}).headers["etag"])

    status, cache = _respond(tmp_path, "index.html", if_none_match=etag)
    assert (status, cache) == (304, _REVALIDATE_CACHE)
