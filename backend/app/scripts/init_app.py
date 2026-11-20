import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.concurrency import asynccontextmanager
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config.setting import settings
from app.core.docs import get_custom_ui_html
from app.core.exceptions import handle_exception
from app.core.http_limit import RateLimiter, build_limiter, http_limit_callback
from app.core.logger import log
from app.utils.common_util import import_module, import_modules_async
from app.utils.console import console_close, console_run

# 模块级可切换限流器：lifespan 启动前为 None（不限流），启动后由 build_limiter 赋值。
# 使用函数依赖而非模块级 Depends(RateLimiter(...)) 的原因：
# register_routers 在 create_app() 中调用（lifespan 之前），
# 而 Limiter 实例依赖运行时 Redis 连接，无法在模块加载时构造。
_limiter: RateLimiter | None = None


async def _rate_limit(request: Request, response: Response) -> None:
    """可切换的全站限流依赖：lifespan 启动后指向真实限流器。"""
    if _limiter is not None:
        await _limiter(request, response)


ROUTE_RATE_LIMIT = Depends(_rate_limit)
"""全站 HTTP 路由统一限流依赖：内置 router 与插件路由共用同一策略。"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    """
    自定义 FastAPI 应用生命周期。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - AsyncGenerator[Any, Any]: 生命周期上下文生成器。
    """
    from app.core.ap_scheduler import SchedulerUtil
    from app.core.database import create_tables
    from app.core.plugin.runtime import get_runtime

    runtime = get_runtime(app)
    plugin_started = False

    try:
        # 表结构由内核建（插件模型已在 create_app 阶段导入并注册到 metadata）；
        # 种子数据与系统配置/字典缓存预热均归属 module_system 插件，在 runtime.start()
        # 内部按「种子 → 插件 start()」顺序完成。
        await create_tables()
        log.info(f"✅ {settings.DATABASE_TYPE}数据库表结构初始化完成")
        await import_modules_async(
            modules=settings.EVENT_LIST, desc="全局事件", app=app, status=True
        )
        log.info("✅ 全局事件模块加载完成")
        redis_client = app.state.redis
        await SchedulerUtil.init_scheduler(redis=redis_client)
        log.info("✅ 定时任务调度器初始化完成")

        global _limiter
        backend = await build_limiter(
            redis_client,
            max_requests=settings.REQUEST_LIMITER_MAX_REQUESTS,
            window_seconds=settings.REQUEST_LIMITER_WINDOW_SECONDS,
        )
        _limiter = RateLimiter(
            backend=backend,
            callback=http_limit_callback,
        )
        if redis_client is not None:
            log.info("✅ 请求限流器初始化完成（RedisLimiter 分布式限流）")
        else:
            log.warning(
                "⚠️ Redis 未启用（REDIS_ENABLE=False）：降级为 InMemoryLimiter 本地限流"
            )

        if runtime is not None:
            await runtime.start(redis=redis_client)
            plugin_started = True
            log.info("✅ 插件运行时启动完成")

        # 启动期一次（非热路径）：槽位缺失＝提供方插件未安装。演示模式/IP 名单等安全开关
        # 会静默失效，必须在启动日志里显式告警，避免被当成"配置读取失败"排查。
        from app.core.plugin.slots import (
            SLOT_CONFIG_PARAMS_PROVIDER,
            SLOT_LOG_OPERATION_SINK,
            SLOT_SCHEDULER_JOB_LOG_SINK,
        )
        from app.core.plugin.slots import (
            get as _get_slot,
        )

        if _get_slot(SLOT_LOG_OPERATION_SINK) is None:
            log.warning(
                "⚠️ 槽位 log.operation_sink 未提供（module_system 插件缺失？）："
                "操作日志仅写文件日志，不落库"
            )
        if _get_slot(SLOT_CONFIG_PARAMS_PROVIDER) is None:
            log.warning(
                "⚠️ 槽位 config.params_provider 未提供（module_system 插件缺失？）："
                "演示模式/IP 白名单/黑名单等中间件策略按默认值（全部关闭/空）执行"
            )
        if _get_slot(SLOT_SCHEDULER_JOB_LOG_SINK) is None:
            log.warning(
                "⚠️ 槽位 scheduler.job_log_sink 未提供（module_task 插件缺失或未定义 "
                "PLUGIN 绑定？）：任务执行日志仅写文件日志，不落库"
            )

        # 导入并显示最终的启动信息面板
        from app.common.enums import EnvironmentEnum

        console_run(
            host=settings.SERVER_HOST,
            port=settings.SERVER_PORT,
            reload=settings.ENVIRONMENT == EnvironmentEnum.DEV,
            database_ready=True,
            redis_ready=True,
            scheduler_ready=SchedulerUtil.is_running(),
            limiter_ready=True,
        )

    except Exception as e:
        log.error(f"❌ 应用初始化失败: {e!s}")
        raise SystemExit(1)

    yield

    try:
        if plugin_started and runtime is not None:
            await runtime.stop()
            log.info("✅ 插件运行时已关闭")
        await SchedulerUtil.shutdown(wait=False)
        log.info("✅ 定时任务调度器已关闭")
        _limiter = None
        log.info("✅ 请求限制器已关闭")
        await import_modules_async(
            modules=settings.EVENT_LIST, desc="全局事件", app=app, status=False
        )
        log.info("✅ 全局事件模块卸载完成")
        console_close()

    except Exception as e:
        log.error(f"❌ 应用关闭过程中发生错误: {e!s}")


def register_middlewares(app: FastAPI) -> None:
    """
    注册全局中间件。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - None
    """
    for middleware in settings.MIDDLEWARE_LIST[::-1]:
        if not middleware:
            continue
        middleware = import_module(middleware, desc="中间件")
        app.add_middleware(middleware)


def register_exceptions(app: FastAPI) -> None:
    """
    统一注册异常处理器。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - None
    """
    handle_exception(app)


def register_routers(app: FastAPI) -> None:
    """
    注册根路由。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - None
    """
    from app.core.discover import get_dynamic_router

    # 先将动态路由注册到应用，使用速率限制器
    # 获取动态路由实例
    app.include_router(
        router=get_dynamic_router(),
        dependencies=[ROUTE_RATE_LIMIT],
    )

    # 插件运行时：发现插件目录 → setup → 导入模型 → 挂载插件路由
    # （限流策略与上方内置 router 完全一致，插件路由不绕过全站限流；
    # WebSocket 路由由 module_ai 插件在 setup 期声明专属的 WebSocketRateLimiter）
    from app.core.plugin.runtime import PluginRuntime

    PluginRuntime().discover_and_setup().mount(app, dependencies=[ROUTE_RATE_LIMIT])


# 带**内容 hash** 的产物名（Vite 的 `xxx.[hash].js`）。这类 URL 的内容与名字一一对应，
# 改一个字节名字就变，所以可以放心永久缓存。其余（`index.html`、`favicon.ico`、
# `manifest.webmanifest`…）没有 hash，必须每次都复验。
_HASHED_NAME = re.compile(r"^(?P<stem>.+)\.(?P<hash>[A-Za-z0-9_-]{8,})\.(?P<ext>[A-Za-z0-9]+)$")
_HASHABLE_EXTS = frozenset(
    {"js", "mjs", "css", "png", "svg", "webp", "jpg", "jpeg", "gif", "ico", "woff", "woff2", "ttf", "eot", "map"}
)


def _is_hashed_asset(name: str) -> bool:
    """``xxx.<hash>.<ext>`` 的产物名判定。

    额外要求 hash 段里**至少有一个数字或大写字母**：Vite 的 hash 是 base64url，含大小写与
    数字；而人手起的名字（``my.component.js``、``site.settings.css``）通常不会。判错的代价
    是**单向**的——把「人手名字」误判成「有 hash」，那个文件就会被永久缓存，以后改了内容
    但名字没变，同事就卡在旧版本；反过来多判几次 304 只是慢一点。所以这里宁可严一点。
    """
    matched = _HASHED_NAME.match(name)
    if matched is None or matched["ext"].lower() not in _HASHABLE_EXTS:
        return False
    return any(ch.isdigit() or ch.isupper() for ch in matched["hash"])


# 入口 HTML 的缓存策略**必须是 no-cache**（= 每次复验，etag 命中回 304）：发新版时 `dist/`
# 整个被替换，旧 chunk 随之下线；若浏览器还在用缓存的旧 `index.html`，它引用的那些 chunk
# 已经 404——表现为**白屏，且刷新也救不回来**。这是「以后发版不能卡住同事」的根基。
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_REVALIDATE_CACHE = "no-cache"


class _StaticFiles(StaticFiles):
    """按**浏览器实际请求的路径**解析静态文件，绕开 ``ROOT_PATH`` 的干扰。

    本应用把 ``root_path`` 用成了**接口前缀**（``.env`` 的 ``ROOT_PATH = /api/v1``，前端也是
    打到 ``/api/v1/*``）：Starlette 的路由靠 ``root_path`` 从 ``scope["path"]`` 里剥前缀，
    ``/api/v1/feishu/...`` 才能对上注册在 ``/feishu/...`` 的路由。

    但 ``Mount`` 会把 ``root_path`` **累加**成 ``<原 root_path><挂载路径>``（``/api/v1/web``），
    再交给 ``StaticFiles``；而 ``StaticFiles`` 是拿 ``root_path`` 去剥 ``scope["path"]`` 的，
    而后者是浏览器给的原样路径 ``/web/index.html``——两者对不上，于是它去找
    ``dist/web/index.html``，**整个前端 404**。

    这里在进 ``StaticFiles`` 之前把 ``root_path`` 与 ``path`` 都归一成「挂载路径 + 相对路径」，
    路径推导即恢复正常。不改 ``ROOT_PATH``：那是接口路径的一部分，动它会波及所有接口。
    （``/static`` 挂载同理，之前一直是坏的，只是没人用到才没暴露。）
    """

    def __init__(self, directory: Path, mount_path: str) -> None:
        super().__init__(directory=directory, html=True)
        self._mount_path = mount_path.rstrip("/")
        self._app_root = settings.ROOT_PATH.rstrip("/")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope = dict(scope)
        path = str(scope["path"])
        # 两种写法都认：`/web/...`（推荐，同事拿到的链接）与 `/api/v1/web/...`（带上了接口前缀）
        if self._app_root and path.startswith(self._app_root + self._mount_path):
            path = path[len(self._app_root) :]
        scope["path"] = path
        scope["root_path"] = self._mount_path
        await super().__call__(scope, receive, send)

    def file_response(
        self,
        full_path: Any,
        stat_result: os.stat_result,
        scope: Any,
        status_code: int = 200,
    ) -> Response:
        """按「有没有内容 hash」下发 Cache-Control（策略与理由见上面的常量）。

        不设这层的话，浏览器对无 `Cache-Control` 的响应会按**启发式**缓存
        （约「距 last-modified 时长的 10%」）——`dist/` 是几天前构建的，就意味着
        **几小时**内不该复验。那正是「部署完新版，同事那边还是旧界面」的来源。
        """
        response = super().file_response(full_path, stat_result, scope, status_code)
        # 304（NotModifiedResponse）也要带同一套头，否则复验一次之后缓存策略又丢了
        response.headers["Cache-Control"] = (
            _IMMUTABLE_CACHE if _is_hashed_asset(Path(full_path).name) else _REVALIDATE_CACHE
        )
        return response


def register_files(app: FastAPI) -> None:
    """
    注册静态资源挂载和文件相关配置。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - None
    """
    # 挂载静态文件目录
    if settings.STATIC_ENABLE:
        # 确保静态资源根目录存在
        settings.STATIC_ROOT.mkdir(parents=True, exist_ok=True)
        app.mount(
            path=settings.STATIC_URL,
            app=_StaticFiles(settings.STATIC_ROOT, settings.STATIC_URL),
            name=settings.STATIC_DIR,
        )

    # 挂载前端构建产物：内网演示由后端一个端口同时提供页面与接口（见 `FRONTEND_ENABLE`）。
    #
    # `html=True` 让目录请求（`/web/`）自动落到 `index.html`。前端是 hash 路由，深链与刷新
    # 请求的都是 `/web/` 本身，所以不需要再写一条「找不到就回 index.html」的回退。
    #
    # 目录不存在时**只告警不挂载**：开发机没跑过 `pnpm build`、或后端镜像里压根没带前端代码，
    # 都不该让服务起不来。同理不加 `check_dir=False`——那会让 `/web/*` 变成 500 而不是 404。
    if settings.FRONTEND_ENABLE:
        frontend_root = settings.FRONTEND_ROOT
        if frontend_root.is_dir():
            app.mount(
                path=settings.FRONTEND_URL,
                app=_StaticFiles(frontend_root, settings.FRONTEND_URL),
                name="frontend",
            )
        else:
            log.warning(
                f"⚠️ 未找到前端构建产物 {frontend_root}：{settings.FRONTEND_URL} 不提供服务"
                "（在 frontend/web 下执行 pnpm build 后重启即可）"
            )

        # 只输主机名与端口的人（`http://<主机>:6100`）本该落到应用上：这条路以前是 404，
        # 同事只能靠别人给的完整链接。用 302 而不是直接返回 index.html——让地址栏跟着变成
        # `/web/`，前端自己的资源相对路径与 `base` 才对得上。
        @app.get("/", include_in_schema=False)
        async def frontend_index() -> RedirectResponse:
            return RedirectResponse(url=f"{settings.FRONTEND_URL}/", status_code=302)


def reset_api_docs(app: FastAPI) -> None:
    """
    使用本地静态资源自定义 API 文档页面（Swagger UI 与 ReDoc）。

    参数:
    - app (FastAPI): FastAPI 应用实例。

    返回:
    - None
    """

    @app.get(str(app.swagger_ui_oauth2_redirect_url), include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get(settings.DOCS_URL, include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=str(app.root_path) + str(app.openapi_url),
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=settings.SWAGGER_JS_URL,
            swagger_css_url=settings.SWAGGER_CSS_URL,
            swagger_favicon_url=settings.FAVICON_URL,
        )

    @app.get(settings.REDOC_URL, include_in_schema=False)
    async def custom_redoc_html():
        return get_redoc_html(
            openapi_url=str(app.root_path) + str(app.openapi_url),
            title=app.title + " - ReDoc",
            redoc_js_url=settings.REDOC_JS_URL,
            redoc_favicon_url=settings.FAVICON_URL,
        )

    @app.get(settings.LJDOC_URL, include_in_schema=False)
    async def custom_ui_html():
        return get_custom_ui_html(
            openapi_url=str(app.root_path) + str(app.openapi_url),
            title=app.title + " - LangJin UI",
            swagger_js_url=settings.CUSTOM_JS_URL,
            swagger_css_url=settings.CUSTOM_CSS_URL,
            swagger_favicon_url=settings.FAVICON_URL,
        )
