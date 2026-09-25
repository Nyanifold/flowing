# Calculator Working Directory

This small Python project demonstrates a runtime working directory (cwd) for
an agent application. The complete calculator implementation and its usage
are included here.

## Structure

~~~text
project/
├── README.md
└── pkg/
    └── calc.py
~~~

The pkg directory intentionally has no __init__.py. Python can import it as a
namespace package when the project root is on sys.path.

## Working-directory concept

An application may provide a cwd value to its agents. Prompts can consume that
value, and delegated agents can use it as the boundary for file operations.
This document's calculator is independent of that orchestration mechanism.
The larger assembly example is described in the [companion guide](../README.md).

## Requirements

Python 3.13 or later is sufficient. The calculator uses no third-party
dependencies, build manifest, or test framework.

## Complete calculator module

Save as the relative filename pkg/calc.py:

~~~python
"""Example project: calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
~~~

The annotations document intended numeric arguments; the functions do not
perform explicit type validation. Addition returns a + b. Division uses true
division and raises ValueError when b equals zero.

## Run and expected output

Run from the project root:

~~~console
$ python -c "from pkg.calc import add, div; print(add(1.5, 2.5)); print(div(10, 4))"
4.0
2.5
~~~

The zero-divisor case:

~~~python
from pkg.calc import div

try:
    div(1, 0)
except ValueError as exc:
    print(exc)
~~~

~~~text
divisor must not be 0
~~~
