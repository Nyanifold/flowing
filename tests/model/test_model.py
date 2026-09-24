"""model.py 测试（T-83 ~ T-90）：ModelConfig 字段级惰性求值与两个 loader。"""

import pytest

from flowing.errors import FlowingError
from flowing.model import ModelConfig, load_model_tags, load_models
from flowing.parsable import Parsable

from fakes import FakeAgent, FakeRuntime


@pytest.fixture
def agent(tmp_path):
    return FakeAgent(FakeRuntime(tmp_path, config={"thinking_budget": 500}), source_dir=tmp_path)


def test_t83_defaults():
    cfg = ModelConfig("m", "p")
    assert cfg.thinking_budget is None
    assert cfg.context_window is None
    assert cfg.max_output_tokens is None
    assert cfg._extra == {}


def test_t84_static_resolve_idempotent(agent):
    cfg = ModelConfig("m", "p", context_window=1000)
    r1 = cfg.resolve(agent)
    r2 = cfg.resolve(agent)
    assert r1 is cfg  # 全静态幂等短路允许返回 self
    assert r2.model == r1.model == "m" and r2.context_window == 1000


def test_t85_parsable_field_resolve(agent):
    agent.budget = 1000
    cfg = ModelConfig("m", "p", thinking_budget=Parsable("{{ agent.budget }}"))
    resolved = cfg.resolve(agent)
    assert resolved is not cfg  # 含 Parsable 必返新实例
    assert resolved.thinking_budget == 1000
    assert isinstance(resolved.thinking_budget, int)
    assert cfg.thinking_budget is not resolved.thinking_budget  # 原 cfg 不变
    assert isinstance(cfg.thinking_budget, Parsable)


def test_t86_resolve_failure_propagates_self_unchanged(agent):
    # 引用不存在的属性：EXPRESSION 经 ChainableUndefined 得 None（不抛）；
    # 用一个必然抛错的求值——调用不存在的方法（AttributeError 上抛）
    cfg = ModelConfig("m", "p", thinking_budget=Parsable("{{ agent.no_such_method() }}"))
    with pytest.raises(Exception):
        cfg.resolve(agent)
    assert isinstance(cfg.thinking_budget, Parsable)  # cfg 本身不被修改


def test_t87_extra_not_resolved(agent):
    p = Parsable("{{ agent.budget }}")
    cfg = ModelConfig("m", "p", extra={"note": p})
    resolved = cfg.resolve(agent)
    assert resolved._extra["note"] is p  # _extra 不参与求值，原样拷贝


# ---------------------------------------------------------------------------
# loader（fixtures/providers）
# ---------------------------------------------------------------------------

def test_t88_load_models(fixtures_dir):
    models = load_models(fixtures_dir / "providers" / "models.yaml")
    assert set(models) == {"sonnet", "deepseek-v4",
                           "deepseek-flash", "deepseek-flash-thinking",
                           "openrouter-gpt6"}
    sonnet = models["sonnet"]
    assert sonnet.provider == "anthropic" and sonnet.model == "claude-sonnet-4-6"
    assert isinstance(sonnet.thinking_budget, Parsable)  # 模板形态字段被包装
    assert sonnet.thinking_budget.type == "EXPRESSION"
    ds = models["deepseek-v4"]
    assert ds.provider == "deepseek-personal" and ds.model == "deepseek-chat"
    assert ds.context_window == 64000  # 静态值保持原样（不包装）
    openrouter = models["openrouter-gpt6"]
    assert openrouter.model == "openai/gpt-6-luna"
    assert openrouter["reasoning.effort"] == "high"
    assert getattr(openrouter, "reasoning.effort") == "high"


def test_t88b_loaded_parsable_field_resolves(fixtures_dir, agent):
    # 加载产物中的 Parsable 字段参与运行时求值（resolve 链路冒烟）
    models = load_models(fixtures_dir / "providers" / "models.yaml")
    resolved = models["sonnet"].resolve(agent)
    assert resolved.thinking_budget == 500  # agent.runtime.config 提供值


def test_t89_unknown_field_goes_to_extra(fixtures_dir):
    models = load_models(fixtures_dir / "providers" / "models.yaml")
    cfg = models["deepseek-v4"]
    assert cfg._extra["team_note"] == "x"  # 不告警、不丢弃
    assert "team_note" not in vars(cfg)  # 扩展字段由 __getattr__ 读取
    assert cfg.team_note == cfg["team_note"] == "x"


def test_model_config_item_access_preserves_declared_fields():
    cfg = ModelConfig(
        "m", "p", thinking_budget=123, context_window=456,
        max_output_tokens=789,
        extra={"note": "extra", "reasoning.effort": "high",
               # Formal fields win over conflicting manual extra keys.
               "model": "shadow-model"},
    )
    assert cfg["model"] == cfg.model == "m"
    assert cfg["provider"] == cfg.provider == "p"
    assert cfg["thinking_budget"] == cfg.thinking_budget == 123
    assert cfg["context_window"] == cfg.context_window == 456
    assert cfg["max_output_tokens"] == cfg.max_output_tokens == 789
    assert cfg.note == cfg["note"] == "extra"
    assert cfg["reasoning.effort"] == getattr(cfg, "reasoning.effort") == "high"
    with pytest.raises(KeyError):
        _ = cfg["missing"]
    with pytest.raises(AttributeError):
        _ = cfg.missing


def test_t89b_deepseek_thinking_entries_split(fixtures_dir):
    """同一 deepseek-v4-flash 的思考开/关两个条目：思考参数进 _extra
    （DeepSeek adapter 解释），无思考条目不携带这些键。"""
    models = load_models(fixtures_dir / "providers" / "models.yaml")
    plain = models["deepseek-flash"]
    thinking = models["deepseek-flash-thinking"]
    assert plain.model == thinking.model == "deepseek-v4-flash"
    assert "thinking" not in plain._extra
    assert "reasoning_effort" not in plain._extra
    assert thinking._extra["thinking"] == "enabled"
    assert thinking._extra["reasoning_effort"] == "high"


def test_t90_load_model_tags(fixtures_dir):
    tags = load_model_tags(fixtures_dir / "providers" / "model-tags.yaml")
    assert tags == {"fast": "deepseek-v4", "high": "sonnet", "default": "fast"}
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items())
