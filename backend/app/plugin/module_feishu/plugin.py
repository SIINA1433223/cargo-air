"""feishu 插件入口。

路由由 ``app/core/discover.py`` 目录扫描挂载（``module_feishu/**/controller.py`` →
容器前缀 ``/feishu``）。

本模块**不落库**：直接打通飞书多维表格 API，飞书即唯一数据源（双向同步天然成立），
故无 ORM 模型可声明，``setup()`` 留空即可。
"""

from __future__ import annotations

from app.core.plugin.base import PluginBase
from app.core.plugin.context import PluginContext


class Plugin(PluginBase):
    """飞书多维表格插件：提供体验更好的前端，读写直接透传飞书。"""

    def setup(self, ctx: PluginContext) -> None:
        """无模型需声明。

        参数:
        - ctx (PluginContext): 注册接口。

        返回:
        - None
        """


# 插件运行时按 ``runtime.ENTRY_VAR == "PLUGIN"`` 取模块级实例；
# 缺少该绑定时入口会被判为不可用（ERROR + 降级为无实例），``setup()`` 不会执行。
PLUGIN = Plugin()
