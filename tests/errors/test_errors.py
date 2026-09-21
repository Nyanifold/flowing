"""errors 模块测试（简报测试清单 E1–E8）。"""

import builtins
from pathlib import Path

import pytest

import flowing.errors as errors
from flowing.errors import (
    AmbiguousToolError,
    ArtifactModifiedError,
    DependencyError,
    DuplicateHookPointError,
    EntryNameConflictError,
    FlowingError,
    Intercepted,
    MissingEnvironmentVariableError,
    MissingFieldError,
    MissingProvideError,
    NameMismatchError,
    ProviderError,
    ProviderTimeoutError,
    QuotaExhaustedError,
    RateLimitedError,
    SignalDeliveryError,
    SignalTimeoutError,
)

# 每个具名类型的最小合法构造（E1 遍历用；中间层无字段，直接无参构造）
_FACTORIES = {
    "FlowingError": lambda: FlowingError("x"),
    "ConfigError": lambda: errors.ConfigError("x"),
    "ConfigNotReadyError": lambda: errors.ConfigNotReadyError(),
    "ConfigNamespaceConflictError": lambda: errors.ConfigNamespaceConflictError("i18n"),
    "ProvideError": lambda: errors.ProvideError("x"),
    "MissingProvideError": lambda: MissingProvideError("x"),
    "HookError": lambda: errors.HookError("x"),
    "UnknownHookPointError": lambda: errors.UnknownHookPointError("before_x"),
    "DuplicateHookPointError": lambda: DuplicateHookPointError("on_signal", "a", "b"),
    "RegistryNotFoundError": lambda: errors.RegistryNotFoundError("x"),
    "RegistryConflictError": lambda: errors.RegistryConflictError("x"),
    "AgentTypeNotFoundError": lambda: errors.AgentTypeNotFoundError("payment-agent"),
    "AgentTypeConflictError": lambda: errors.AgentTypeConflictError("default::payment-agent"),
    "SkillNotFoundError": lambda: errors.SkillNotFoundError("sum"),
    "SkillNameConflictError": lambda: errors.SkillNameConflictError("default::sum"),
    "ToolError": lambda: errors.ToolError("x"),
    "MissingSchemaError": lambda: errors.MissingSchemaError("backup", "cmd"),
    "ToolNotFoundError": lambda: errors.ToolNotFoundError("make-payment"),
    "ToolNameConflictError": lambda: errors.ToolNameConflictError("make-payment"),
    "UnknownToolError": lambda: errors.UnknownToolError("delete_everything"),
    "AmbiguousToolError": lambda: AmbiguousToolError("tools/make_payment.py"),
    "AmbiguousMcpSourceError": lambda: errors.AmbiguousMcpSourceError("github"),
    "MissingMcpSourceError": lambda: errors.MissingMcpSourceError("github"),
    "ResourceError": lambda: errors.ResourceError("x"),
    "ResourceNameConflictError": lambda: errors.ResourceNameConflictError("db"),
    "ResourceNotFoundError": lambda: errors.ResourceNotFoundError("db"),
    "ProviderError": lambda: ProviderError("x"),
    "ContextLengthError": lambda: errors.ContextLengthError("x"),
    "RequestTooLargeError": lambda: errors.RequestTooLargeError("x"),
    "RateLimitedError": lambda: RateLimitedError("x"),
    "QuotaExhaustedError": lambda: QuotaExhaustedError("x"),
    "ServerError": lambda: errors.ServerError("x"),
    "NetworkError": lambda: errors.NetworkError("x"),
    "ProviderTimeoutError": lambda: ProviderTimeoutError("x"),
    "AuthenticationError": lambda: errors.AuthenticationError("x"),
    "InvalidRequestError": lambda: errors.InvalidRequestError("x"),
    "ContentPolicyError": lambda: errors.ContentPolicyError("x"),
    "MissingEnvironmentVariableError": lambda: MissingEnvironmentVariableError("X", "e"),
    "ProviderNameConflictError": lambda: errors.ProviderNameConflictError("deepseek"),
    "DependencyError": lambda: DependencyError("guard", ["flowing.comm"]),
    "CommError": lambda: errors.CommError("x"),
    "DuplicateEndpointError": lambda: errors.DuplicateEndpointError("payment-guard"),
    "SignalDeliveryError": lambda: SignalDeliveryError("ui", "perm"),
    "SignalTimeoutError": lambda: SignalTimeoutError("ui", 0.05),
    "EntryNameConflictError": lambda: EntryNameConflictError("pay", "skill"),
    "FormatError": lambda: errors.FormatError("x"),
    "MissingFieldError": lambda: MissingFieldError("system_prompt", "Foo"),
    "MissingContextError": lambda: errors.MissingContextError(),
    "ReservedAttributeError": lambda: errors.ReservedAttributeError("env"),
    "NameMismatchError": lambda: NameMismatchError("payment", "foo", "@/foo.fya"),
    "CompileError": lambda: errors.CompileError("x"),
    "ArtifactModifiedError": lambda: ArtifactModifiedError(Path("x")),
    # 推测点 X6 / X7 新增的具名类型（持久化层）
    "FormatVersionError": lambda: errors.FormatVersionError(Path("f.jsonl"), 99, 1),
    "CorruptionError": lambda: errors.CorruptionError(Path("f.jsonl"), 3),
    "UnpairedToolCallError": lambda: errors.UnpairedToolCallError("call-x"),
    "Intercepted": lambda: Intercepted("r"),
}


def test_e8_all_names_importable_and_expected():
    """E8：__all__ 全部名字可导入且为预期类（Registry* 一族新增 6 个）。

    注：``EntryNameConflictError`` 按 py-spec 原样不在 ``__all__`` 中（直挂根
    的跨正交类），仍可显式导入；``StateKeyError`` 已退役删除（D6/D8）。
    """
    assert len(errors.__all__) == 55
    excluded = {"EntryNameConflictError"}
    assert set(errors.__all__) == set(_FACTORIES) - excluded
    for name in errors.__all__:
        obj = getattr(errors, name)
        assert isinstance(obj, type), name
    # 不在 __all__ 但按 spec 存在的直挂根类
    assert issubclass(errors.EntryNameConflictError, FlowingError)
    # StateKeyError 退役（D6 删拦截 + D8 读回 KeyError/AttributeError 后无触发点）
    assert not hasattr(errors, "StateKeyError")


def test_e1_all_named_errors_are_flowing_errors_except_intercepted():
    """E1：__all__ 全部具名异常实例 isinstance FlowingError，唯 Intercepted 除外。"""
    for name in errors.__all__:
        e = _FACTORIES[name]()
        if name == "Intercepted":
            assert not isinstance(e, FlowingError)
        else:
            assert isinstance(e, FlowingError), name


def test_e2_intercepted_is_plain_exception_with_fields():
    """E2：Intercepted 直挂 Exception，reason/payload 字段就位。"""
    assert issubclass(Intercepted, Exception)
    assert not issubclass(Intercepted, FlowingError)
    e = Intercepted("r")
    assert e.reason == "r"
    assert e.payload is None
    e2 = Intercepted("r", payload={"tool": "t"})
    assert e2.payload == {"tool": "t"}
    assert str(e2) == "r"


def test_e3_provider_error_base_fields():
    """E3：ProviderError 通用五字段默认 None，关键字传参逐字段可读。"""
    e = ProviderError("msg")
    assert str(e) == "msg"
    assert e.provider is None
    assert e.model is None
    assert e.status_code is None
    assert e.request_id is None
    assert e.retry_after is None
    e2 = ProviderError(
        "msg", provider="deepseek", model="deepseek-chat",
        status_code=429, request_id="req-1", retry_after=1.5,
    )
    assert (e2.provider, e2.model, e2.status_code, e2.request_id, e2.retry_after) == (
        "deepseek", "deepseek-chat", 429, "req-1", 1.5,
    )


def test_e4_provider_timeout_is_not_builtin_timeout():
    """E4：ProviderTimeoutError 不继承内置 TimeoutError（M-02 改名裁决）。"""
    e = ProviderTimeoutError("timeout")
    assert isinstance(e, ProviderTimeoutError)
    assert not isinstance(e, builtins.TimeoutError)


def test_e5_quota_exhausted_is_not_rate_limited():
    """E5：QuotaExhaustedError 刻意不做 RateLimitedError 子类（M-06 裁决）。"""
    e = QuotaExhaustedError("quota")
    assert not isinstance(e, RateLimitedError)


def test_e6_leaf_fields():
    """E6：叶子异常结构化字段逐类断言。"""
    assert MissingProvideError("x").key == "x"
    e = MissingEnvironmentVariableError("X", "e")
    assert (e.var_name, e.entry) == ("X", "e")
    e = DuplicateHookPointError("n", "a", "b")
    assert (e.name, e.existing_by, e.new_by) == ("n", "a", "b")
    e = EntryNameConflictError("pay", "skill")
    assert (e.alias, e.kind) == ("pay", "skill")
    e = MissingFieldError("system_prompt", "Foo")
    assert (e.field, e.agent_type) == ("system_prompt", "Foo")
    e = NameMismatchError("payment", "foo", "@/foo.fya")
    assert (e.declared, e.inferred, e.source) == ("payment", "foo", "@/foo.fya")
    assert DependencyError("guard", ["flowing.comm"]).missing == ["flowing.comm"]
    e = SignalDeliveryError("ui", "perm")
    assert (e.target, e.signal_type) == ("ui", "perm")
    e = SignalTimeoutError("ui", 0.05)
    assert (e.target, e.timeout) == ("ui", 0.05)
    assert AmbiguousToolError("p").path == "p"
    assert ArtifactModifiedError(Path("x")).path == Path("x")
    e = errors.FormatVersionError(Path("f.jsonl"), 99, 1)
    assert (e.path, e.found, e.supported) == (Path("f.jsonl"), 99, 1)
    e = errors.CorruptionError(Path("f.jsonl"), 3)
    assert (e.path, e.lineno) == (Path("f.jsonl"), 3)
    # Registry* 一族：字段与双挂
    assert errors.AgentTypeNotFoundError("payment-agent").name == "payment-agent"
    assert errors.AgentTypeConflictError("default::x").key == "default::x"
    e = errors.SkillNotFoundError("sum", detail="tried: a, b")
    assert (e.name, e.detail) == ("sum", "tried: a, b")
    assert errors.SkillNameConflictError("default::sum").key == "default::sum"
    assert isinstance(errors.ToolNotFoundError("t"), errors.RegistryNotFoundError)
    assert isinstance(errors.ToolNameConflictError("t"), errors.RegistryConflictError)
    assert isinstance(errors.AgentTypeNotFoundError("a"), errors.RegistryNotFoundError)
    assert isinstance(errors.AgentTypeConflictError("a"), errors.RegistryConflictError)
    assert isinstance(errors.SkillNotFoundError("s"), errors.RegistryNotFoundError)
    assert isinstance(errors.SkillNameConflictError("s"), errors.RegistryConflictError)


def test_leaf_messages_carry_natural_language_hints():
    """X14 澄清：叶子异常消息给自然语言关键提示（英文），字段为权威。"""
    samples = [
        (MissingProvideError("user_id"), "user_id"),
        (errors.ToolNotFoundError("make-payment"), "make-payment"),
        (errors.ConfigNamespaceConflictError("i18n"), "i18n"),
        (errors.DuplicateEndpointError("payment-guard"), "payment-guard"),
        (errors.FormatVersionError(Path("f.jsonl"), 99, 1), "99"),
        (errors.CorruptionError(Path("f.jsonl"), 3), "3"),
    ]
    for e, hint in samples:
        assert hint in str(e), f"{type(e).__name__} 消息缺少关键提示: {e}"
