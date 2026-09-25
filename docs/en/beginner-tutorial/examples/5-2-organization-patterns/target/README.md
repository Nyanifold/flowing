# Calculator Example Project (target)

A small calculator example project that serves as the sandbox / working
directory for the **5-2 Organization Patterns** example of the tutorial: the
agents read and write files in this directory.

## Project structure

```text
target/
├── README.md        # this file
├── notes/
│   ├── adder.md     # glossary note: "adder" -> add()
│   └── divider.md   # glossary note: "divider" -> div()
└── pkg/
    └── calc.py      # calculator implementation (add, div)
```

- `pkg/calc.py` — the only source file; defines the `add()` and `div()` functions.
- `notes/adder.md` — short glossary note defining the term "adder".
- `notes/divider.md` — short glossary note defining the term "divider".
- `notes/` — directory holding human-readable notes (not imported by Python).
- `pkg/` — directory holding the Python module; note there is no `__init__.py` here.

## API reference

All functions live in `pkg/calc.py`. The module docstring is
`"Example project: calculator."`.

### `add(a: float, b: float) -> float`

Returns the sum of `a` and `b`.

| Parameter | Type    | Description        |
| --------- | ------- | ------------------ |
| `a`       | `float` | first operand      |
| `b`       | `float` | second operand     |

- **Returns:** `float` — the value of `a + b`.

### `div(a: float, b: float) -> float`

Returns the quotient of `a` divided by `b`.

| Parameter | Type    | Description        |
| --------- | ------- | ------------------ |
| `a`       | `float` | dividend           |
| `b`       | `float` | divisor            |

- **Returns:** `float` — the value of `a / b`.
- **Raises:** `ValueError("divisor must not be 0")` when `b == 0`.

## Documentation / Notes

The complete glossary content is included here. “Adder” means the
<code>add(a, b)</code> function, which returns the sum of two values. “Divider”
means the <code>div(a, b)</code> function, which returns the quotient and raises
<code>ValueError("divisor must not be 0")</code> when the divisor is zero. These
are plain-language definitions; Python does not import or execute them.

## Usage

Run the following command with this project's root as the working directory:

```console
$ python -c "from pkg.calc import add, div; print(add(2, 3)); print(div(6, 4))"
5
1.5
```

The same calls can be made in a Python session:

```python
from pkg.calc import add, div

print(add(2, 3))
print(div(6, 4))
```

Expected output:

```text
5
1.5
```

The `pkg/` directory has no `__init__.py`; Python treats it as a namespace
package. The import works when this project's root is the current directory,
or when that root is on `sys.path`. The complete multi-agent walkthrough is
described in [the companion example guide](../README.md).

The complete calculator implementation shown in this document is:

```python
"""Example project: calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
```

The two glossary notes are also fully reproduced here.

### notes/adder.md

~~~markdown
# adder

An adder adds two values using the add operation.
~~~

### notes/divider.md

~~~markdown
# divider

A divider divides two values using the div operation. A zero divisor raises
ValueError("divisor must not be 0").
~~~

## Notes / limitations

- **No tests** — there is no test suite in this directory.
- **No packaging** — `pkg/` has no `__init__.py` and there is no build
  manifest (`pyproject.toml`, `setup.py`, ...), so nothing is installable.
- **No CLI entry point** — the functions can only be used by importing them;
  there is no `__main__` or command-line interface.
- **Plain floats only** — `add()` and `div()` are annotated for `float`
  operands and return a `float`; no other input validation or coercion is
  performed (an invalid type fails with the usual Python `TypeError`).
- **No lint configuration** — no linter or formatter config is present.
- **Python 3.13** is the version used with this example.
