"""阶段 3 CliTool / RequestTool 测试（W23/W24）：测试清单 47–49。

CliTool：Jinja2 命令模板 + shlex.quote 自动转义 + ``| raw`` 告警 +
非零退出码不等于 error。RequestTool：args→请求映射四规则（路径参数排除 /
method 决定去向 / auth 优先 / expected_status 之外 → error 结果）+
output schema 字段提取。HTTP 服务端为自研 demo（``tests/fixtures/http/``，
本地回环，不起真实外网）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from flowing.errors import MissingSchemaError
from flowing.tool import CliTool, RequestTool, ToolDefinition, ToolRegistry

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures" / "http"))
from demo_server import create_server   # noqa: E402

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def http_server():
    server, thread, base_url = create_server()
    yield base_url
    server.shutdown()
    server.server_close()


def _cli_tool(command: str, params: dict, **kwargs) -> CliTool:
    return CliTool(definition=ToolDefinition(
        name="t", description="", params_schema=params), command=command, **kwargs)


# ---------------------------------------------------------------------------
# 清单 47：CliTool / RequestTool 未声明 args → MissingSchemaError
# ---------------------------------------------------------------------------


def test_cli_tool_requires_args():
    with pytest.raises(MissingSchemaError):
        CliTool(definition=ToolDefinition(name="t", description=""),
                command="echo hi")


def test_cli_tool_illegal_shell_fails_fast():
    """非法 shell 声明 → 构造期 FormatError（加载期 fail-fast，不留到执行期）。"""
    from flowing.errors import FormatError

    with pytest.raises(FormatError, match="非法 shell"):
        _cli_tool("echo hi", {"x": {"type": "string"}}, shell="zsh")


def test_request_tool_requires_args():
    with pytest.raises(MissingSchemaError):
        RequestTool(definition=ToolDefinition(name="t", description=""),
                    url="http://127.0.0.1/x")


# ---------------------------------------------------------------------------
# 清单 48：CliTool 执行（fixtures fya/tools/run-tests/TOOL.fya）
# ---------------------------------------------------------------------------


async def test_cli_tool_auto_escapes_inserted_values():
    """插入值自动转义：含 ``;`` 的值被 shlex.quote 包成字面量，不注入。"""
    tool = _cli_tool("echo {{ word }}", {"word": {"type": "string"}})
    result = await tool({"word": "a;echo HACKED"})
    assert result.status == "completed"
    assert result.output["stdout"] == "a;echo HACKED\n"
    assert result.output["exit_code"] == 0


async def test_cli_tool_raw_filter_bypasses_and_warns(caplog):
    """显式 ``| raw`` 旁路转义并产生告警。"""
    tool = _cli_tool("echo {{ word | raw }}", {"word": {"type": "string"}})
    with caplog.at_level(logging.WARNING, logger="flowing.tool"):
        result = await tool({"word": "'raw value'"})
    assert result.status == "completed"
    assert result.output["stdout"] == "raw value\n"   # 未转义：引号被 shell 消费
    assert any("| raw" in r.message for r in caplog.records)


async def test_cli_tool_run_tests_fixture(tmp_path, fixtures_dir):
    """run-tests/TOOL.fya 真实执行：``cd {{ working_dir }} && pytest``。

    工作目录带空格（转义生效的实证）；非零退出码照常作为输出数据返回。
    """
    project = tmp_path / "proj with space"
    (project / "src").mkdir(parents=True)
    (project / "src" / "test_ok.py").write_text("def test_ok(): pass\n",
                                                encoding="utf-8")
    registry = ToolRegistry()
    tool = registry.get("run-tests", source_dir=fixtures_dir / "fya" / "tools")
    assert isinstance(tool, CliTool)
    result = await tool({"working_dir": str(project), "test_path": "src/"})
    assert result.status == "completed"
    assert result.output["exit_code"] == 0, result.output["stderr"]
    # 非零退出码 ≠ error：指向不存在的测试路径 → pytest 退出码非 0
    failing = await tool({"working_dir": str(project), "test_path": "nope/"})
    assert failing.status == "completed"
    assert failing.output["exit_code"] != 0


# ---------------------------------------------------------------------------
# 清单 49：RequestTool（fixtures fya/tools/recommend/TOOL.fya + demo server）
# ---------------------------------------------------------------------------


async def test_request_tool_four_mapping_rules(
        http_server, fixtures_dir, monkeypatch):
    """recommend/TOOL.fya 全链：注册表命中 → 执行 → 四规则断言。"""
    port = http_server.rsplit(":", 1)[1]
    monkeypatch.setenv("DEMO_HTTP_PORT", port)
    monkeypatch.setenv("DEMO_HTTP_TOKEN", "right-token")
    monkeypatch.setenv("DEMO_HTTP_TOKEN_WRONG", "wrong-token")
    registry = ToolRegistry()
    tool = registry.get("recommend", source_dir=fixtures_dir / "fya" / "tools")
    assert isinstance(tool, RequestTool)
    result = await tool({"category": "电子产品", "limit": 3})
    assert result.status == "completed", result.error
    out = result.output
    # output schema 提取字段（extra / method / path / x-api-key 被忽略）
    assert set(out) == {"category", "body", "query", "authorization"}
    assert out["category"] == "电子产品"
    # 路径参数排除：category 不进 body/query；limit 按 method（POST）进 body
    assert out["body"] == {"limit": 3}
    assert out["query"] == {}
    # auth 优先于 headers 的冲突头
    assert out["authorization"] == "Bearer right-token"


async def test_request_tool_unexpected_status_is_error_result(http_server):
    """expected_status 之外的状态码 → status="error" 结果（不抛异常）。"""
    tool = RequestTool(
        definition=ToolDefinition(name="t", description="",
                                  params_schema={"x": {"type": "string"}}),
        url=f"{http_server}/always-conflict", method="POST")
    result = await tool({"x": "1"})
    assert result.status == "error"
    assert "409" in result.error


async def test_request_tool_get_goes_query(http_server):
    """GET 方法：非路径参数默认走 query string。"""
    tool = RequestTool(
        definition=ToolDefinition(
            name="t", description="",
            params_schema={"q": {"type": "string"},
                           "limit": {"type": "integer", "default": 5}}),
        url=f"{http_server}/search", method="GET")
    result = await tool({"q": "手机", "limit": 5})
    assert result.status == "completed"
    assert result.output["query"] == {"q": "手机", "limit": "5"}
    assert result.output["body"] == {}


async def test_request_tool_explicit_query_body_override(http_server):
    """query/body 声明显式覆盖 method 默认去向（POST 下强制走 query）。"""
    tool = RequestTool(
        definition=ToolDefinition(
            name="t", description="",
            params_schema={"category": {"type": "string"},
                           "verbose": {"type": "boolean", "default": False},
                           "limit": {"type": "integer", "default": 5}}),
        url=f"{http_server}/recommend/{{{{ category }}}}", method="POST",
        query=["verbose"])
    result = await tool({"category": "书", "verbose": True, "limit": 5})
    assert result.status == "completed"
    assert result.output["query"] == {"verbose": "true"}
    assert result.output["body"] == {"limit": 5}
