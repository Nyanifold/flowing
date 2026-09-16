"""Providers 机制核测试（T01–T18；T07/T11 的 provider_gen 侧归 turn 引擎）。

覆盖：ProviderConfig/{{env.VAR}} 加载、Usage 映射、Provider 懒实例化、
register_provider 双形态与冲突、FLOWING_PROVIDER_MODULES 自动发现、
load_provider_candidates 扫描期解析。OpenAI/Anthropic 家族的 wire 映射
测试（T19/T20）在 test_provider_adapters.py；真实端点冒烟（T130–T134）
在 test_providers_live.py（网络门控，默认跳过）。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from flowing.errors import (
    FlowingError,
    ProviderNameConflictError,
    RateLimitedError,
)
from flowing.providers import (
    FakeProvider,
    Provider,
    ProviderConfig,
    ProviderDelta,
    ProviderRegistry,
    ProviderResponse,
    Usage,
    load_provider_candidates,
    register_provider,
)
from flowing.providers.provider import _provider_adapters
from flowing.providers.openai_completions import OpenAICompletionsProvider

from flowing.context import Context
from flowing.message import Message, MessageKind, TextBlock
from flowing.model import ModelConfig


FIXTURE_YAML = Path(__file__).parent.parent / "fixtures" / "providers" / "providers.yaml"


def _ctx() -> Context:
    return Context(system_prompt=[], tools=[], messages=[])


def _model() -> ModelConfig:
    return ModelConfig(model="fake-model", provider="fake")


# ── T01/T02：{{env.VAR}} 加载期替换 ─────────────────────────────────────────

def test_t01_missing_env_var_warns_and_empties(fixtures_dir, monkeypatch):
    """T01：api_key 引用缺失的 env → 加载不中断：替换为空串并告警
    （配多个条目只用一个时，其余条目的环境变量不必齐备）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="DEEPSEEK_API_KEY"):
        candidates = load_provider_candidates(
            fixtures_dir / "providers" / "providers.yaml")
    assert candidates["deepseek-personal"][1]["api_key"] == ""
    assert candidates["anthropic-main"][1]["api_key"] == ""


def test_t01b_mixed_entries_independent(fixtures_dir, monkeypatch):
    """T01b：两条目各看各的环境变量——有值的正常替换，缺失的空串
    + 告警，互不影响。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-a")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="ANTHROPIC_API_KEY"):
        candidates = load_provider_candidates(
            fixtures_dir / "providers" / "providers.yaml")
    assert candidates["deepseek-personal"][1]["api_key"] == "sk-test-a"
    assert candidates["anthropic-main"][1]["api_key"] == ""


def test_t02_non_env_placeholder_preserved(fixtures_dir, monkeypatch):
    """T02："{{other.x}}" 非 {{env. 前缀 → 原样保留，不报错不替换。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-b")
    candidates = load_provider_candidates(fixtures_dir / "providers" / "providers.yaml")
    assert set(candidates) == {"deepseek-personal", "anthropic-main", "literal-holder"}
    _, config = candidates["deepseek-personal"]
    assert config["api_key"] == "sk-test-a"  # {{env.VAR}} 已替换
    _, literal_config = candidates["literal-holder"]
    assert literal_config["note"] == "{{other.x}}"  # 原样保留


def test_t02b_env_reference_embedded_in_string(monkeypatch, tmp_path):
    """{{env.VAR}} 可嵌在更长字符串中做全局替换（纯字符串替换语义）。"""
    monkeypatch.setenv("HOST", "example.com")
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(
        'x:\n  adapter: deepseek\n  base_url: "https://{{env.HOST}}/v1"\n',
        encoding="utf-8",
    )
    candidates = load_provider_candidates(yaml_path)
    assert candidates["x"][1]["base_url"] == "https://example.com/v1"


def test_load_missing_file_returns_empty(tmp_path):
    """providers.yaml 不存在 → 空候选清单（Runtime 零配置启动容忍）。"""
    assert load_provider_candidates(tmp_path / "nope.yaml") == {}


# ── T03：Usage 归一映射（经 OpenAI 家族 mock transport 驱动） ──────────────

class _MockTransportProvider(OpenAICompletionsProvider):
    """测试用：覆写网络点 _post 返回录制响应。"""

    name = "mock-openai"

    def __init__(self, config, canned):
        super().__init__(config)
        self._canned = canned
        self.sent_bodies: list[dict] = []

    async def _post(self, path: str, body: dict) -> dict:
        self.sent_bodies.append(body)
        return self._canned


async def test_t03_usage_mapping():
    """T03：prompt_tokens=10(cached 4)/completion_tokens=5 → 归一七字段。"""
    canned = {
        "model": "deepseek-chat",
        "choices": [{"message": {"role": "assistant", "content": "ok"},
                      "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 4},
            "prompt_cache_hit_tokens": 4,
            "prompt_cache_miss_tokens": 6,
        },
    }
    provider = _MockTransportProvider(ProviderConfig({"api_key": "sk-x"}), canned)
    response = await provider.generate(_ctx(), _model())
    usage = response.message.usage
    assert usage.input == 10
    assert usage.fresh_input == 6
    assert usage.cache_read == 4
    assert usage.output == 5
    assert usage.total_tokens == 15
    assert usage.raw["prompt_cache_hit_tokens"] == 4  # 原始字段全保留
    assert response.finish is True


# ── T04/T12/T13：ProviderRegistry 懒实例化 ──────────────────────────────────

async def test_t04_lazy_instantiation(fixtures_dir, monkeypatch, tmp_path):
    """T04：Runtime 构造只建候选清单不实例化；get("x") 后仅 "x" 一个实例。"""
    import flowing

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-b")
    providers_yaml = fixtures_dir / "providers" / "providers.yaml"
    (tmp_path / "main.py").write_text(textwrap.dedent(f"""\
        import flowing
        async def main():
            runtime = flowing.Runtime()
            runtime.set_providers({str(providers_yaml)!r})
            return runtime
        """))
    runtime = await flowing.launch(tmp_path)
    registry = runtime.provider_registry
    assert registry._instances == {}  # 构造期零实例化
    provider = registry.get("deepseek-personal")
    assert list(registry._instances) == ["deepseek-personal"]  # 仅该条目实例化
    assert provider.config["api_key"] == "sk-a"
    await runtime.shutdown()


def test_t12_registry_get_cache_and_keyerror():
    """T12：两次 get("x") 同一实例（is）；get("ghost") 抛 KeyError。"""
    registry = ProviderRegistry({"x": (FakeProvider, ProviderConfig())})
    assert registry.get("x") is registry.get("x")
    with pytest.raises(KeyError):
        registry.get("ghost")


def test_t13_failure_not_cached():
    """T13：构造抛错的条目不缓存失败，修复后重试成功。"""
    calls = []

    class FlakyProvider(FakeProvider):
        name = "flaky"

        def __init__(self, config):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            super().__init__(config)

    registry = ProviderRegistry({"x": (FlakyProvider, ProviderConfig())})
    with pytest.raises(RuntimeError):
        registry.get("x")
    assert registry._instances == {}  # 失败不缓存
    assert isinstance(registry.get("x"), FlakyProvider)  # 重试成功


# ── T05/T06：generate 错误分类与 cancel 契约形状 ────────────────────────────

async def test_t05_rate_limited_no_retry():
    """T05：mock transport 429 → RateLimitedError，不自动重试。"""
    from flowing.providers.openai_completions import _HttpResponseError

    calls = []

    class _429(_MockTransportProvider):
        name = "mock-429"

        async def _post(self, path, body):
            calls.append(1)
            raise _HttpResponseError(429, {"error": {"message": "rate limited"}})

    provider = _429(ProviderConfig({"api_key": "sk-x"}), {})
    with pytest.raises(RateLimitedError):
        await provider.generate(_ctx(), _model())
    assert len(calls) == 1  # 不自动重试


async def test_t06_cancelled_response_shape():
    """T06：cancel 置位 → 返回 cancelled=True 响应而非异常（契约形状）。"""
    provider = FakeProvider()

    async def _gen(context, model):
        return ProviderResponse(message=None, finish=False, cancelled=True)

    provider.generate_fn = _gen
    response = await provider.generate(_ctx(), _model())
    assert response.cancelled is True
    assert response.finish is False  # 不变量：cancelled=True 时 finish 保持 False
    assert response.message is None


# ── T08：get_credential ─────────────────────────────────────────────────────

def test_t08_get_credential():
    """T08：config 含 api_key → 返回之；空 config → None。"""
    assert FakeProvider(ProviderConfig({"api_key": "sk-x"})).get_credential() == "sk-x"
    assert FakeProvider().get_credential() is None


# ── T09/T10/T11：FakeProvider ───────────────────────────────────────────────

async def test_t09_uninjected_raises():
    """T09：未注入 generate_fn → FlowingError，消息含 generate_fn。"""
    with pytest.raises(FlowingError, match="generate_fn"):
        await FakeProvider().generate(_ctx(), _model())


async def test_t10_received_spy():
    """T10：注入后两次 generate → received 长度 2，按调用次序。"""
    provider = FakeProvider()

    async def _gen(context, model):
        return ProviderResponse(
            message=Message(kind=MessageKind.PROVIDER,
                            content=[TextBlock(text="ok")]),
            finish=True, model="fake")

    provider.generate_fn = _gen
    c1, c2 = _ctx(), _ctx()
    await provider.generate(c1, _model())
    await provider.generate(c2, _model())
    assert provider.received == [c1, c2]


async def test_t11_stream_fallback_records_once():
    """T11（provider 侧）：仅注入 generate_fn → generate_stream 基类回退，
    一条完整 delta，received 长度 1（不重复记录）。"""
    provider = FakeProvider()

    async def _gen(context, model):
        return ProviderResponse(
            message=Message(kind=MessageKind.PROVIDER,
                            content=[TextBlock(text="hello world")]),
            finish=True, model="fake")

    provider.generate_fn = _gen
    deltas = [d async for d in provider.generate_stream(_ctx(), _model())]
    assert len(deltas) == 1
    assert deltas[0].kind == "text" and deltas[0].text == "hello world"
    assert len(provider.received) == 1  # 记录由 generate() 完成，不重复追加


# ── T14/T15/T16：register_provider ──────────────────────────────────────────

def test_t14_name_conflict():
    """T14：同名再注册 → ProviderNameConflictError 且 e.name 命中。"""

    @register_provider
    class _DupA(FakeProvider):
        name = "t14-dup"

    with pytest.raises(ProviderNameConflictError) as exc_info:
        @register_provider
        class _DupB(FakeProvider):
            name = "t14-dup"

    assert exc_info.value.name == "t14-dup"


def test_t15_override_warns_and_replaces():
    """T15：override=True → 注册表指向新类 + 一条警告。"""

    @register_provider
    class _OvA(FakeProvider):
        name = "t15-ov"

    with pytest.warns(UserWarning):
        @register_provider(override=True)
        class _OvB(FakeProvider):
            name = "t15-ov"

    assert _provider_adapters["t15-ov"] is _OvB


def test_t16_value_error_and_identity_return():
    """T16：装饰非 Provider 子类 / 缺 name → ValueError；原样返回被装饰类。"""
    with pytest.raises(ValueError):
        register_provider(object)
    with pytest.raises(ValueError):
        @register_provider
        class _NoName(FakeProvider):
            name = ""

    @register_provider
    class _Ok(FakeProvider):
        name = "t16-ok"

    assert _provider_adapters["t16-ok"] is _Ok  # 原样返回，未包装


# ── T17：FLOWING_PROVIDER_MODULES 自动发现 ──────────────────────────────────

def _write_pkg(root: Path, name: str, body: str) -> None:
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(body, encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="依赖 Runtime 自动发现，暂以 xfail 标注")
async def test_t17_provider_modules_autodiscovery(tmp_path, monkeypatch):
    """T17：FLOWING_PROVIDER_MODULES="pkg_a:pkg_b" → 两包被 import、adapter
    可按名引用；import 抛错的包警告并跳过不中断。"""
    _write_pkg(tmp_path, "pkg_a", textwrap.dedent("""\
        from flowing.providers import FakeProvider, register_provider

        @register_provider
        class PkgAProvider(FakeProvider):
            name = "pkg-a-adapter"
        """))
    _write_pkg(tmp_path, "pkg_b", textwrap.dedent("""\
        from flowing.providers import FakeProvider, register_provider

        @register_provider
        class PkgBProvider(FakeProvider):
            name = "pkg-b-adapter"
        """))
    _write_pkg(tmp_path, "pkg_broken", "raise RuntimeError('import broken')")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("FLOWING_PROVIDER_MODULES", "pkg_a:pkg_broken:pkg_b")

    import flowing

    async def _run():
        (tmp_path / "main.py").write_text(textwrap.dedent("""\
            import flowing
            async def main():
                return flowing.Runtime()
            """), encoding="utf-8")
        with pytest.warns(UserWarning, match="pkg_broken"):
            runtime = await flowing.launch(tmp_path)
        return runtime

    runtime = await _run()
    assert "pkg_a" in sys.modules and "pkg_b" in sys.modules
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(
        "a:\n  adapter: pkg-a-adapter\nb:\n  adapter: pkg-b-adapter\n",
        encoding="utf-8")
    candidates = load_provider_candidates(yaml_path)
    assert set(candidates) == {"a", "b"}  # 两包 adapter 可按名引用
    await runtime.shutdown()


# ── T18：未知 adapter 扫描期快速失败 ────────────────────────────────────────

def test_t18_unknown_adapter_keyerror(tmp_path):
    """T18：未知 adapter 名 → load_provider_candidates 抛 KeyError。"""
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text("x:\n  adapter: ghost-adapter\n", encoding="utf-8")
    with pytest.raises(KeyError):
        load_provider_candidates(yaml_path)
