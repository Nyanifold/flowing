# 示例项目：计算器

这是一个极简 Python 计算器，只公开两个函数：`add` 做加法，`div` 做除法。本文给出完整模块、输入、输出和异常行为；阅读与复现不需要查找其他文件。

## 完整模块

以下代码可保存为相对文件名 `pkg/calc.py`。`pkg` 是普通目录，不需要额外的 `__init__.py`；运行导入示例时，Python 的当前工作目录应是包含 `pkg` 的项目根目录。

```python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

## API 与复现

| 函数 | 参数 | 返回值 | 异常 |
| --- | --- | --- | --- |
| `add(a, b)` | `a: float`、`b: float` | `a + b`，类型为 `float` | 无 |
| `div(a, b)` | `a: float`、`b: float` | `a / b`，类型为 `float` | `b == 0` 时抛出 `ValueError("除数不能为 0")` |

两个函数的完整类型签名分别为 `add(a: float, b: float) -> float` 与 `div(a: float, b: float) -> float`。使用以下输入：

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))
```

预期输出：

```text
3
3.5
0.5
```

除数为零时，函数会抛出异常，而不是返回特殊数值：

```python
from pkg.calc import div

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

预期输出：

```text
ValueError: 除数不能为 0
```

## 术语

`adder` 是 `add` 的非正式称呼，`divider` 是 `div` 的非正式称呼；它们不是函数、类或配置项。实际可调用的名称只有 `add` 与 `div`。

## 环境与限制

- Python 3 即可；示例只使用标准库，没有第三方依赖。
- 运行命令时，请让当前工作目录保持为包含 `pkg` 的项目根目录；本文未要求切换目录。
- 这是可导入的模块，不提供 CLI、安装器或测试套件。
