"""
后端接口测试入口。

注意：测试函数使用同步 `def`，由 TestClient 驱动；勿对用例本身使用 `async def`。
执行示例: `pytest tests/test_main.py` 或 `pytest tests/`
"""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner


def test_check_readiness(test_client: TestClient) -> None:
    """
    校验 ``/common/health/ready/``：数据库与 Redis（若启用）均可达时返回 200 与 checks 结构。
    """
    response = test_client.get("/common/health/ready/")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] is not None
    assert "checks" in body["data"]
    assert body["data"]["checks"].get("database") is True


def test_check_health(test_client: TestClient) -> None:
    """
    校验 `/common/health/` 返回统一成功响应结构。

    参数:
    - test_client (TestClient): pytest 注入的客户端。

    返回:
    - None
    """
    response = test_client.get("/common/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["code"] == 0
    assert body["msg"] == "系统健康"
    assert body["data"] is True
    assert body["status_code"] == 200


def test_plugin_runtime_mounted() -> None:
    """插件运行时已挂载到 app.state，且发现了 app/plugin 下的全部插件。"""
    from app.core.plugin.runtime import get_runtime
    from main import create_app

    app = create_app()
    runtime = get_runtime(app)
    assert runtime is not None
    assert {e.plugin_dir.name for e in runtime.registry.entries} >= {
        "ai",
        "example",
        "generator",
        "task",
    }


# ==================== `run` 命令的热重载开关（`--reload/--no-reload`）====================
#
# 演示部署跑的是 `--env=dev`，于是 uvicorn 开着热重载：改一个 .py（连测试文件都算）就重启，
# 重启的那两秒里所有在途请求失败，浏览器端报 axios 的 Network Error。关掉它需要 plist 能传
# `--no-reload`，而这条链路只有真启动服务才看得到——重启太贵，这里把 `uvicorn.run` 换成探针，
# 直接断言「传进去的 reload 是几」。


def _run_cli(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> dict[str, object]:
    """跑一次 `run` 命令，返回交给 `uvicorn.run` 的关键字参数。"""
    import main

    captured: dict[str, object] = {}
    monkeypatch.setattr(main.uvicorn, "run", lambda **kwargs: captured.update(kwargs))
    # `run` 会直接写 os.environ，用 setenv 提前占位，由 monkeypatch 在用例结束后还原
    monkeypatch.setenv("ENVIRONMENT", "dev")

    result = CliRunner().invoke(main.fastapiadmin_cli, ["run", *args])
    assert result.exit_code == 0, result.output
    return captured


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        # 不传时跟随环境：dev 开、prod 关（保持既有行为不变）
        (["--env=dev"], True),
        (["--env=prod"], False),
        # 显式覆盖：演示部署用 `--env=dev --no-reload`，环境语义不变、只关热重载
        (["--env=dev", "--no-reload"], False),
        (["--env=dev", "--reload"], True),
        (["--env=prod", "--reload"], True),
    ],
)
def test_run_热重载开关(
    args: list[str], expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run_cli(monkeypatch, args)["reload"] is expected


# 运行所有测试
if __name__ == "__main__":
    pytest.main(["-v", "tests/test_main.py"])
