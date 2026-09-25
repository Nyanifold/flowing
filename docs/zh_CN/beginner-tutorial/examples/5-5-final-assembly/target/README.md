# 计算器工作目录

这是一个用于说明 Agent 应用运行时工作目录（cwd）的极简 Python 项目。
本文内含完整的计算器实现和用法。

## 结构

~~~text
project/
├── README.md
└── pkg/
    └── calc.py
~~~

pkg 目录有意不包含 __init__.py。当项目根目录位于 sys.path 中时，Python
可以将它作为命名空间包导入。

## 工作目录概念

应用可以向 Agent 提供 cwd 值。提示词可以读取该值，受委派的 Agent 可以
以此限制文件操作范围。本文的计算器与该编排机制相互独立。更完整的总装
示例见[配套说明](../README.md)。

## 环境要求

Python 3.13 或更高版本即可。计算器不依赖第三方库、构建清单或测试框架。

## 完整计算器模块

保存为相对文件名 pkg/calc.py：

~~~python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
~~~

类型标注描述预期数值入参；函数不做显式类型校验。加法返回 a + b。除法
使用真除法，b 等于 0 时抛出 ValueError。

## 运行与预期输出

在项目根目录运行：

~~~console
$ python -c "from pkg.calc import add, div; print(add(1.5, 2.5)); print(div(10, 4))"
4.0
2.5
~~~

除数为零时：

~~~python
from pkg.calc import div

try:
    div(1, 0)
except ValueError as exc:
    print(exc)
~~~

~~~text
除数不能为 0
~~~
