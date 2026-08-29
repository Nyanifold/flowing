"""阶段 1 params.py 测试（T-01 ~ T-21）：schema 桥接与类型安全键。"""

import pytest
from pydantic import ValidationError

from flowing.errors import FormatError
from flowing.params import (
    ARG_SHORTHAND,
    SCHEMA_KEYWORDS,
    TYPE_ALIASES,
    ConfigKey,
    InjectionKey,
    _coerce,
    _parse_type_expr,
    apply_param_overrides,
    expand_args_schema,
    schema_to_model,
)

# ---------------------------------------------------------------------------
# expand_args_schema（T-01 ~ T-03）
# ---------------------------------------------------------------------------

def test_t01_expand_shorthand_and_literal():
    props = expand_args_schema({"a": "str", "b": 20, "c": {"type": "string"}})
    assert props["a"] == {"type": "string"}
    assert props["b"] == {"type": "integer", "default": 20}
    assert props["c"] == {"type": "string"}


def test_t02_expand_none_value_fail_fast():
    with pytest.raises(FormatError):
        expand_args_schema({"x": None})


def test_t03_expand_empty_dict_kept_as_any():
    props = expand_args_schema({"x": {}})
    assert props["x"] == {}


# ---------------------------------------------------------------------------
# _parse_type_expr（T-04 ~ T-07）
# ---------------------------------------------------------------------------

def test_t04_union_exprs():
    assert _parse_type_expr("str | None") == {"type": ["string", "null"]}
    assert _parse_type_expr("int | str") == {"type": ["integer", "string"]}


def test_t05_generic_exprs():
    assert _parse_type_expr("list[str]") == {"type": "array", "items": {"type": "string"}}
    # dict 的键值类型不进 schema（object 键恒为字符串）
    assert _parse_type_expr("dict[str, int]") == {"type": "object"}


def test_t06_nested_composition():
    assert _parse_type_expr("list[str | None]") == {
        "type": "array",
        "items": {"type": ["string", "null"]},
    }


def test_t07_unparseable_fail_fast():
    for bad in ("foo[[", "list[", "", "foo |", "| str", "list[]"):
        with pytest.raises(FormatError):
            _parse_type_expr(bad)


# ---------------------------------------------------------------------------
# schema_to_model（T-08 ~ T-12）
# ---------------------------------------------------------------------------

def test_t08_basic_model_and_required_derivation():
    Model = schema_to_model("M", {"a": {"type": "string"}, "b": {"type": "integer", "default": 1}})
    m = Model.model_validate({"a": "x"})
    assert m.b == 1
    with pytest.raises(ValidationError):
        Model.model_validate({})


def test_t09_nullable_optional_field():
    Model = schema_to_model("M", {"a": {"type": ["string", "null"], "default": None}})
    assert Model.model_validate({}).a is None
    assert Model.model_validate({"a": None}).a is None
    assert Model.model_validate({"a": "x"}).a == "x"


def test_t10_out_of_subset_fail_fast():
    with pytest.raises(FormatError) as exc:
        schema_to_model("M", {"a": {"oneOf": [{"type": "string"}]}})
    assert "a" in str(exc.value) and "oneOf" in str(exc.value)


def test_t11_constraint_mapping():
    Model = schema_to_model(
        "M",
        {
            "amount": {"type": "number", "minimum": 0.01, "maximum": 100},
            "name": {"type": "string", "minLength": 2, "maxLength": 5, "pattern": "^[a-z]+$"},
            "color": {"type": "string", "enum": ["r", "g"]},
        },
    )
    Model.model_validate({"amount": 1.0, "name": "abc", "color": "r"})
    with pytest.raises(ValidationError):
        Model.model_validate({"amount": 0.001, "name": "abc", "color": "r"})
    with pytest.raises(ValidationError):
        Model.model_validate({"amount": 1.0, "name": "A", "color": "r"})
    with pytest.raises(ValidationError):
        Model.model_validate({"amount": 1.0, "name": "abc", "color": "b"})


def test_t12_type_alias_normalization_and_input_immutability():
    props = {"a": {"type": "str"}}
    Model = schema_to_model("M", props)
    assert Model.model_validate({"a": "x"}).a == "x"
    assert props == {"a": {"type": "str"}}  # 入参 dict 未被修改


def test_t12b_type_expr_via_schema_to_model():
    # 含 | / [ ] 的复合形态经 _parse_type_expr 展开后参与桥接
    Model = schema_to_model("M", {"xs": {"type": "list[str]"}, "n": {"type": "int | None", "default": None}})
    m = Model.model_validate({"xs": ["a", "b"]})
    assert m.xs == ["a", "b"] and m.n is None
    assert Model.model_validate({"xs": [], "n": 3}).n == 3


def test_t12c_nullable_list_members_alias_normalized():
    # 列表形态成员同样过 TYPE_ALIASES 归一；未知成员 fail-fast
    Model = schema_to_model("M", {"a": {"type": ["str", "null"], "default": None}})
    assert Model.model_validate({"a": "x"}).a == "x"
    assert Model.model_validate({"a": None}).a is None
    with pytest.raises(ValidationError):
        Model.model_validate({"a": 1})  # str | None 生效（未静默落 Any）
    with pytest.raises(FormatError):
        schema_to_model("M", {"a": {"type": ["strng", "null"]}})


# ---------------------------------------------------------------------------
# apply_param_overrides（T-13 ~ T-16）
# ---------------------------------------------------------------------------

def test_t13_sparse_merge_and_input_immutability():
    base = {"amount": {"type": "number", "minimum": 0.01}}
    new = apply_param_overrides(base, {"amount": {"description": "支付金额"}})
    assert new["amount"] == {"type": "number", "minimum": 0.01, "description": "支付金额"}
    assert base == {"amount": {"type": "number", "minimum": 0.01}}


def test_t14_unknown_keyword_fail_fast():
    with pytest.raises(FormatError):
        apply_param_overrides({"amount": {"type": "number"}}, {"amount": {"descrition": "笔误"}})


def test_t15_unknown_param_is_addition():
    new = apply_param_overrides({"a": {"type": "string"}}, {"new_param": {"type": "integer"}})
    assert new["new_param"] == {"type": "integer"}


def test_t16_default_override_semantics():
    base = {"a": {"type": "string", "default": "x"}}
    assert apply_param_overrides(base, {"a": {"default": "y"}})["a"]["default"] == "y"
    # 补丁不含 default → 原 default 沿用
    assert apply_param_overrides(base, {"a": {"description": "d"}})["a"]["default"] == "x"


# ---------------------------------------------------------------------------
# _coerce（T-17）
# ---------------------------------------------------------------------------

def test_t17_coerce():
    assert _coerce("42", {"type": "number"}) == 42.0
    # 无法兼容转换时保留原值，不报错
    assert _coerce("abc", {"type": "integer"}) == "abc"


# ---------------------------------------------------------------------------
# InjectionKey / ConfigKey（T-18 ~ T-21）
# ---------------------------------------------------------------------------

def test_t18_injection_key_hash():
    assert hash(InjectionKey[str]("locale")) == hash("locale")


def test_t19_injection_key_eq_and_repr():
    k = InjectionKey[str]("locale")
    assert k == "locale"
    assert k == InjectionKey[int]("locale")  # 运行期不校验泛型 T
    assert repr(k) == "InjectionKey('locale')"
    assert str(k) == "locale"   # str(key) 归一点落键名（与裸 str 同槽位）


def test_t20_config_key():
    k = ConfigKey[int]("retry.max_attempts")
    assert k == "retry.max_attempts"
    assert hash(k) == hash("retry.max_attempts")
    assert str(k) == "retry.max_attempts"   # 与 InjectionKey.__str__ 同语义


def test_t21_key_and_str_share_dict_slot():
    d = {InjectionKey("x"): 1}
    assert d["x"] == 1
    d["x"] = 2
    assert d[InjectionKey("x")] == 2 and len(d) == 1


def test_w03_constants():
    assert ARG_SHORTHAND["str"] == "string" and ARG_SHORTHAND["dict"] == "object"
    assert TYPE_ALIASES["int"] == "integer"
    assert "oneOf" not in SCHEMA_KEYWORDS and "minimum" in SCHEMA_KEYWORDS
