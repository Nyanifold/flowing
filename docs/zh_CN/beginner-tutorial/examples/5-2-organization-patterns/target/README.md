# 极简计算器示例（target）

## 1. 项目简介

这是一个**极简的 Python 计算器样例**，属于教学示例：它是“5-2 组织模式（organization-patterns）”
一课的 `target` 样本目录，用来演示一个最小可用的包结构（`pkg/` + `notes/`）以及术语笔记的组织方式。

项目只做两件事：两数相加（`add`）与两数相除（`div`）。整个目录中**只有一个源码文件** `pkg/calc.py`，
没有任何第三方依赖、入口脚本或测试代码。

## 2. 目录结构

```
target/
├── README.md          # 本文件：项目说明（项目简介、用法、API、术语表）
├── notes/             # 术语笔记目录
│   ├── adder.md       # 术语“adder”→ pkg/calc.py 的 add(a, b)
│   └── divider.md     # 术语“divider”→ pkg/calc.py 的 div(a, b)
└── pkg/               # 唯一的代码包
    └── calc.py        # 唯一源码文件：模块 docstring 为“示例项目：计算器。”
```

说明：

- `pkg/` 下**没有** `__init__.py`（依赖 Python 3 的命名空间包机制被导入）。
- 目录内**没有** `main.py` / `__main__.py`、没有配置文件（如 `requirements.txt`、`pyproject.toml`），
  也没有任何测试文件。

## 3. 环境要求

- **Python 3**（本 README 中的示例在 Python 3.13.6 下验证通过；命名空间包导入需要 Python 3.3+）。
- **仅使用标准库**，代码中没有任何 `import`，因此**无任何第三方依赖**。
- 不需要安装依赖，也就**没有** `requirements.txt` / `pyproject.toml` 等配置文件。

## 4. 快速开始

### 4.1 一行命令运行

在 `target` 目录下执行：

```bash
python -c "from pkg.calc import add, div; print(add(1,2)); print(div(6,3))"
```

实际输出：

```
3
2.0
```

> 注意：`div(6, 3)` 的结果是 `2.0`，因为 `div` 内部使用 `/` 做真除法，**始终返回 float**。

### 4.2 Python 代码片段

```python
from pkg.calc import add, div

print(add(1, 2))        # 3
print(add(1.5, 2.5))    # 4.0
print(div(6, 3))        # 2.0
print(div(7, 2))        # 3.5
```

实际输出：

```
3
4.0
2.0
3.5
```

### 4.3 处理除零异常

当 `div` 的第二个参数（除数）为 `0` 时，会抛出 `ValueError("除数不能为 0")`，应当显式捕获：

```python
from pkg.calc import div

try:
    div(1, 0)
except ValueError as e:
    print("ValueError:", e)   # ValueError: 除数不能为 0
```

实际输出：

```
ValueError: 除数不能为 0
```

## 5. API 说明

以下函数均定义在 `pkg/calc.py` 中（模块 docstring：“示例项目：计算器。”）。

### 5.1 总览

| 函数 | 签名 | 返回值 | 异常 |
| --- | --- | --- | --- |
| `add` | `add(a: float, b: float) -> float` | 两数之和，即 `a + b` | 无 |
| `div` | `div(a: float, b: float) -> float` | 两数之商，即 `a / b`（float） | `b == 0` 时抛出 `ValueError("除数不能为 0")` |

### 5.2 `add(a, b)`

```python
def add(a: float, b: float) -> float:
    return a + b
```

- **参数**：`a`，`b` —— 两个加数。
- **返回值**：`a + b`。类型标注为 `float`；由于函数体只是 `a + b`，
  若传入两个 `int`，返回的也是 `int`（例如 `add(1, 2)` 得到 `3`）；传入浮点数时得到 `float`（例如 `add(1.5, 2.5)` 得到 `4.0`）。
- **异常**：无（函数体内没有显式校验）。

### 5.3 `div(a, b)`

```python
def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

- **参数**：`a` —— 被除数；`b` —— 除数。
- **返回值**：`a / b`，使用真除法，**结果总是 float**（例如 `div(6, 3)` 得到 `2.0`）。
- **异常**：当 `b == 0` 时，抛出 `ValueError("除数不能为 0")`（错误信息为中文，且不会执行除法）。
- **注意**：`b == 0` 的判断对 `0`、`0.0` 等与 0 相等的数字都成立。

## 6. 术语表

| 术语 | 含义 |
| --- | --- |
| adder | 调用 <code>add(a, b)</code> 得到两个数之和。 |
| divider | 调用 <code>div(a, b)</code> 得到两个数之商；除数为 0 时抛出 <code>ValueError("除数不能为 0")</code>。 |

简言之：**adder → `add`**，**divider → `div`**。

## 7. 说明与约定

- 本目录是**教学示例**，目的是演示代码模块与术语定义的组织方式，而不是提供可发布的库。
- **没有入口脚本**：目录内没有 `main.py` / `__main__.py`，`python pkg/calc.py` 也不会输出任何内容
  （`calc.py` 只定义函数，没有执行副作用）。
- **没有测试**：目录内不存在任何测试文件，示例结果需自行运行验证。
- **`pkg/` 下没有 `__init__.py`**：`pkg` 依赖 Python 3 的命名空间包机制被导入，
  因此导入行为依赖于当前工作目录 / `sys.path`：
  - 从包含 <code>pkg/</code> 的项目根目录运行时，可使用 <code>from pkg.calc import add, div</code>。
  - 若从其他工作目录导入，需确保该项目根目录位于 <code>sys.path</code> 中，否则可能出现
    <code>ModuleNotFoundError: No module named 'pkg'</code>。

计算器模块的完整实现如下：

```python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

术语定义也完整列在这里：**adder** 是返回两数之和的 <code>add(a, b)</code>；
**divider** 是返回两数之商的 <code>div(a, b)</code>，除数为 0 时抛出
<code>ValueError("除数不能为 0")</code>。这些定义是普通说明文字，不会由 Python 导入或执行。

两条术语笔记的完整内容如下。

### notes/adder.md

~~~markdown
# adder

adder 使用加法操作将两个值相加。
~~~

### notes/divider.md

~~~markdown
# divider

divider 使用除法操作计算两个值的商。除数为 0 时会抛出
ValueError("除数不能为 0")。
~~~
