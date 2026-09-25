# Example project: a calculator

This is a minimal Python calculator with two public functions: `add` performs addition, and `div` performs division. This page includes the complete module, inputs, outputs, and error behavior; it does not require looking up any other file.

## Complete module

Save the following code under the relative filename `pkg/calc.py`. The `pkg` directory does not need an `__init__.py`; run the import examples with the project root (the directory containing `pkg`) as Python's current working directory.

```python
"""Example project: a calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
```

## API and reproduction

| Function | Parameters | Return value | Exception |
| --- | --- | --- | --- |
| `add(a, b)` | `a: float`, `b: float` | `a + b`, as a `float` | None |
| `div(a, b)` | `a: float`, `b: float` | `a / b`, as a `float` | Raises `ValueError("divisor must not be 0")` when `b == 0` |

The complete type signatures are `add(a: float, b: float) -> float` and `div(a: float, b: float) -> float`. Use these inputs:

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))
```

Expected output:

```text
3
3.5
0.5
```

For a zero divisor, the function raises an exception instead of returning a special value:

```python
from pkg.calc import div

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

Expected output:

```text
ValueError: divisor must not be 0
```

## Terminology

`adder` is an informal name for `add`, and `divider` is an informal name for `div`; neither is a function, class, or configuration entry. The callable names are `add` and `div`.

## Requirements and limits

- Python 3 is sufficient. The example uses only the standard library and has no third-party dependencies.
- Keep the project root (the directory containing `pkg`) as the current working directory when running the import example; this page does not ask you to change directories.
- This is an importable module. It provides no CLI, installer, or test suite.
