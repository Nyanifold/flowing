"""``.fya`` 显式编译器——构建期把声明式 Agent 编译为同目录 ``.py``。

.. rubric:: 功能介绍

本模块是「``.fya`` → Agent 类」的**合成层本体**（三层架构：parser
字面层 → 装配层 → 本模块合成层）。两个入口同源：

- **内存形态**（:func:`compile_fya_class`）：``.fya`` → 内存中的 Agent
  类——运行期的唯一合成点（``Runtime.get_agent_class`` 路径形态、
  ``mount()``、装配层资源引用全部经此）；
- **落盘形态**（:func:`compile_fya_file`）：= 内存合成 + 写出同目录
  ``.py``（去 ``.fya`` 后缀，兼容 ``from payment import PaymentAgent``），
  产物可被 linter / type checker / mypy 静态分析——生产部署与 CI 推荐。

（旧设计中的 Import Hook / ``flowing.importer`` 形态已删除——运行期
一律走字符串名惰性解析，不需要 import 期钩子；用户手写 Python 直接引用
fya 定义的类时走显式编译产物。）

.. rubric:: 设计动机

- **同目录产物**（comment §6.3/§100.2 覆盖旧 ``flowing build`` +
  ``.flowing-build/`` 方案）：``payment.fya`` → ``payment.py``，标准
  import 链无需任何 hook 即可工作；
- **hash 防覆盖闸**（P3-10 裁决）：meta 写在**产物同目录**的
  ``.flowing.meta.yaml``（每目录一份，条目以 ``.fya`` 文件名为键），
  三字段 ``fya_hash`` / ``py_hash`` / ``compiler_version``。
  两个 hash 均取**语义口径**：``fya_hash`` 哈希解析前端产出的结构
  （注释/空行变化不触发重编译）；``py_hash`` 哈希产物 ``.py`` 的
  AST（``ast.parse`` 后规范化转储）——格式化改动不算「外部修改」，
  语义被手改才 → :class:`flowing.errors.ArtifactModifiedError` 中止，
  **不静默覆盖**（编译产物可读但不应手改；手改请转正为手写子类，
  03 §9.3）；
- **不允热重启**：编译只发生在构建/启动阶段，运行期不监听文件变更
  （comment §6.5）——并发模型无需处理「运行中 Agent vs 重新解析的类」。

.. rubric:: 不变量与互斥

- 同一逻辑的 ``.fya`` 与手写 ``.py`` **等价互斥**（03 §9.3）；同名并存时
  ``.fya`` 优先并告警（告警通道属 import 侧规约）。
- 编译是**幂等**的：``fya_hash`` 未变且 ``compiler_version`` 一致 →
  跳过；冲突中止后重跑可继续，已产出文件不回滚。
- 非行为：不删除无对应 ``.fya`` 的孤儿 ``.py``；不编译手写 ``.py``
  Agent；不拉起 Runtime、不执行 ``main``。

.. seealso::

    - :func:`flowing.interfaces.cli.cmd_compile` —— 本模块的 CLI 壳。
    - :mod:`flowing.parser` —— 字面层前端（``FyaDocument`` 来源）。
    - :mod:`flowing.errors` —— `CompileError` / `ArtifactModifiedError`。
"""

from pathlib import Path

from flowing.errors import ArtifactModifiedError

__all__ = [
    "COMPILER_VERSION",
    "compile_fya_class",
    "compile_fya_file",
    "compile_project",
]

COMPILER_VERSION: str = "0.1.0"
"""编译器版本——写入 meta 的 ``compiler_version`` 字段。

**升版策略（P3-10 裁决）**：版本是缓存键的一部分——meta 中记录的
``compiler_version`` 与当前值不同 → 视同 ``fya_hash`` 变化，**强制
重编译**（防发射格式漂移后旧产物被静默沿用）。框架升级的代价是全
项目 ``.fya`` 首次编译时一次性集体重跑，幂等且廉价。
"""


def compile_fya_class(fya_path: Path) -> type:
    """``.fya`` → 内存中的 Agent 类（**运行期唯一合成点**）。

    .. rubric:: 功能介绍

    时序：``parser.parse_fya``（字面层，文本 → ``FyaDocument``）→ 装配
    （具名块填回、EntryRef 判别、``args`` 经
    :func:`flowing.params.expand_args_schema` 归一化 +
    :func:`flowing.params.schema_to_model` 桥接为 ``args_model``）→
    合成（生成 Agent 子类：类属性注入 ``source_file``/``description``/
    ``system_prompt`` 等，``$script`` 块的 ``setup``/``@on`` 成员并入
    类体）。产物是普通 Python 类，与手写子类同一类模型（等价互斥）。

    .. rubric:: 行为规约

    - 同步、纯内存：不写任何文件（落盘形态是 :func:`compile_fya_file`
      = 本函数 + 写出 ``.py``）。
    - 每次调用现场合成，无缓存（调用方的注册表/惰性解析层负责去重）。
    - ``.fya`` 与同名手写 ``.py`` 并存时不归本函数管（``fya`` 优先并
      告警的裁决在 ``Runtime.get_agent_class`` 的解析侧）。

    :param fya_path: 源 ``.fya`` 路径。
    :return: 合成的 Agent 子类。
    :raises flowing.errors.CompileError: 解析或合成失败。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.parser.parse_fya``（字面层）、
      ``flowing.params.expand_args_schema`` / ``schema_to_model``
      （args 桥接）
    - 被调：``flowing.runtime.Runtime.get_agent_class``（路径形态类型
      解析）、``Runtime.mount``（经 ``get_agent_class``）、
      :func:`compile_fya_file`（落盘形态的合成步）

    .. seealso:: :func:`compile_fya_file` —— 落盘形态。
    """
    # parse_fya -> 装配（具名块填回 / EntryRef 判别 / args 桥接）-> 合成类
    ...


def compile_fya_file(fya_path: Path) -> Path:
    """编译单个 ``.fya`` 为同目录 ``.py``，含 meta hash 校验。

    .. rubric:: 功能介绍

    时序四步：

    1. **合成**：经 :func:`compile_fya_class` 得到内存类（字面层 +
      装配 + 合成一步完成）；
    2. **发射**：生成 ``.py`` 源码——Parsable 值以**类体赋值**形式写出
       （如 ``system_prompt = Parsable('$./system-prompt.md')``），
       ``$script`` 块原样嵌入；
    3. **meta 校验**：读**同目录** ``.flowing.meta.yaml``（每目录一份，
       条目以 ``fya_path.name`` 为键）中本文件的条目——
       产物 ``.py`` 已存在且记录的 ``py_hash``（AST 口径）与实际不符 →
       抛 :class:`flowing.errors.ArtifactModifiedError`（不覆盖）；
       ``fya_hash``（解析结构口径）未变且 ``compiler_version`` 一致 →
       跳过发射，直接返回产物路径（幂等）；版本不同 → 强制重编译
       （见 :data:`COMPILER_VERSION`）；
    4. **写盘**：写产物 ``.py`` 并更新 meta 条目三字段
       （``fya_hash`` / ``py_hash`` / ``compiler_version``）。

    :param fya_path: 源 ``.fya`` 路径。
    :return: 产物 ``.py`` 的路径（无论本次是否重编译）。
    :raises flowing.errors.ArtifactModifiedError: 产物被外部修改
      （``py_hash`` 不匹配）——报错中止，不静默覆盖。
    :raises flowing.errors.CompileError: 解析或发射失败。

    .. rubric:: 测试案例

    - 前置：合法 ``payment.fya``，无既有产物 → 操作：编译 → 期望：同目录
      出现 ``payment.py``，meta 三字段写入；再编译一次 → 期望：no-op。
    - 前置：手工改动产物 ``.py`` → 操作：编译 → 期望：
      ``ArtifactModifiedError``，产物未被覆盖。

    .. rubric:: 调用关系（审计）

    - 调用：:func:`compile_fya_class`（合成，每次编译）；meta 读写
      （yaml/hash 库属实现细节）
    - 被调：:func:`compile_project`（时机：项目级编译逐文件）

    .. seealso:: :func:`compile_project` —— 项目级入口。
    """
    product = fya_path.with_suffix(".py")   # 同目录去 .fya 后缀
    # 第 1-2 步：compile_fya_class 合成 + 发射（类体赋值形式写出）
    # 第 3 步：meta 校验（py_hash 不匹配 -> raise ArtifactModifiedError(
    # product)；fya_hash 未变 -> return product）
    # 第 4 步：写产物 + 更新 meta 三字段
    return product


def compile_project(path: Path) -> list[Path]:
    """项目级编译：递归扫描 ``*.fya`` 并逐文件编译。

    .. rubric:: 功能介绍

    ``flowing compile <path>`` 的唯一编译入口。meta 随产物走——每个
    ``.fya`` 的 hash 条目写入其**所在目录**的 ``.flowing.meta.yaml``
    （P3-10 裁决，不再集中于项目根）。逐个调用
    :func:`compile_fya_file`；任一文件抛
    `ArtifactModifiedError` 则**中止**（已产出文件不回滚——编译幂等，
    重跑可继续）。

    :param path: 项目根目录。
    :return: 全部产物 ``.py`` 路径（按扫描序，``sorted`` 保证稳定）。
    :raises flowing.errors.ArtifactModifiedError: 首个 hash 冲突处中止。

    .. rubric:: 行为规约

    - 边缘情况：无 ``.fya`` 文件 → 返回空列表（调用方 CLI 打印
      「无可编译文件」并正常退出）。
    - 重复执行：无变更时全量命中 ``fya_hash``，等价 no-op。
    - 非行为：不清理孤儿 ``.py``；不编译手写 ``.py``。

    .. rubric:: 测试案例

    - 前置：项目含两个 ``.fya`` → 操作：编译两次 → 期望：第一次返回
      两个产物路径且 meta 写入；第二次返回同样两个路径且为 no-op。

    .. rubric:: 调用关系（审计）

    - 调用：:func:`compile_fya_file`（时机：逐文件）
    - 被调：``flowing.interfaces.cli.cmd_compile``（时机：子命令 ``compile``
      分发，同步直调、不经事件循环）

    .. seealso:: :func:`flowing.interfaces.cli.cmd_compile` —— CLI 壳与退出码映射。
    """
    fya_files = sorted(path.rglob("*.fya"))   # 第 1 步：递归扫描（稳定序）
    products: list[Path] = []
    for fya in fya_files:
        products.append(compile_fya_file(fya))   # meta 随产物同目录
    return products
