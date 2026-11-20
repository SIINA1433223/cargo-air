"""威达业务工作台插件入口。"""

from __future__ import annotations

from app.core.plugin.base import PluginBase
from app.core.plugin.context import PluginContext

from .router import WeidaRouter


class Plugin(PluginBase):
    """把 `/api/v1/weida/*` 挂到应用上。"""

    def setup(self, ctx: PluginContext) -> None:
        ctx.add_router(WeidaRouter)


PLUGIN = Plugin()
