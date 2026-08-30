"""``flowing.runtime`` —— Runtime 对象图根、唯一入口 ``launch``、``@`` 上下文与 provide-inject 链终点。

.. rubric:: 功能介绍

本模块是 Flowing 框架核心层（三层架构最底层）的对象图根模块，承载：

- ``Runtime``：子项目实例、对象图根与唯一全局容器（``_nodes`` / ``_plugins`` /
  ``tool_registry`` / ``_provided`` / ``_config_overrides`` / ``_config_namespaces`` /
  ``_resources`` / provider 候选清单 / agent 池注册表）。
- ``flowing.launch``：Runtime 的**唯一创建入口**；``@`` 项目根上下文的唯一登记点。
- ``flowing.resolve``：模块级 ``@/`` 路径解析。
- ``ProvideNode`` 协议与 ``inject_from``：provide-inject 统一上溯算法。
  **canonical home 已迁至 ``flowing.provide``**（S-43 裁决③：共享符号下沉叶子
  模块，破 agent↔runtime 循环依赖）；本模块 import + 再导出（``__all__``
  保留），旧引用 ``flowing.runtime.ProvideNode`` / ``flowing.runtime.inject_from``
  保持有效。

.. rubric:: 本模块必须覆盖的专属规约角度

**1. ``launch`` 唯一入口与 ``@`` 的 contextvar 隔离**

- ``flowing.launch(path, **kwargs)`` 是创建 Runtime 的唯一入口。绕过它直接
  ``Runtime()`` → 无 ``@`` 上下文 → ``Runtime.__init__`` 抛 ``RuntimeError``。
- ``@`` 上下文载体是模块级 ``_current_project_root``（``ContextVar``），在 asyncio 中
  per-Task 隔离：**一个进程可有多个 Runtime**（多 Runtime 场景），并发 ``launch``
  各自安全；spawn 的子 Task 继承正确值；``launch`` 返回后 ``reset`` 不影响子 Task。
- ``@`` 两条通道同源：模块级 ``resolve()`` 与 ``Runtime.__init__`` 读同一 contextvar；
  ``resolve()`` 在 ``launch`` 登记后**立即可用**（``Runtime()`` 构造之前也行）；
  ``Runtime()`` 构造时把上下文固化到 ``self.project_root``。
- ``@`` 指向 **flowing 子项目目录**（在宿主工程顶层或子目录均可）；宿主大工程自己的
  根是应用层概念，经 ``provide`` 注入，不进 ``@/``。
- 真正的强隔离需求（多租户、A/B 测试）用**子进程**，不靠多 Runtime。

**2. Runtime 生命周期时序不变量**

术语：本模块多处出现的**「常驻工作循环 Task」（工作循环 / work loop）** =
每个 Agent 的常驻 asyncio Task（``Agent._work_loop``：
``while True: 消息 = await queue.get(); 跑 Turn 循环``），与 **Turn
循环**（单条消息内的 LLM↔工具往返，即 ReAct 式循环）相对——工作循环
管「这个 Agent 活着、持续消费自己的消息队列」，Turn 循环管「处理
一条消息要几轮模型调用」。create/recover 管线第 10 步启动它，
``destroy()`` 取消它。

标准顺序（``12 §32.2`` 合并时序）：

.. code-block:: text

    runtime = Runtime()                     # @ 自动绑定；一进程可有多个
    runtime.use(Plugin...)                  # 插件 install，可分批（R1：只注册）
    可选初始化                               # set_model_tags / set_persist_dir / provide /
                                            # register_resource
    Provider 扫描（懒）                      # providers.yaml + FLOWING_PROVIDER_MODULES
                                            # → 候选清单 name → (adapter_class, config)
    agent 池扫描（懒）                       # 读 core 名录（全局 state）→ 逐个开 session 目录
                                            # → {agent_id → 元数据} 注册表
    新建：await runtime.mount("@/root.fya")  → create_agent(parent_id=None)
    恢复：await runtime.recover_agent(agent_id)
    await runtime                           # 进程存活，阻塞至 shutdown
    await runtime.shutdown()                # 优雅关闭

- 新建 vs 恢复是**子项目 ``main()`` 的策略**（如 ``main(resume: str | None = None)``），
  框架不特殊处理 ``--resume`` 之类的参数——它经 ``launch(path, resume=...)`` 原样透传。
- ``mount()`` 是**可选**步骤，可多次调用（多根并存）：Agent 根
  （``mount("@/root.fya")``）、Workflow 根（``mount("@/flow.py")``）、
  无默认节点（不 mount，按需 ``create_agent()``）三种启动形态。
- **空 Runtime**（未 mount）合法：有插件、provide 值、Resource 注册表，但没有 Agent、
  没有工作循环 Task；``await runtime`` 依然有效（只等 ``_shutdown_event``）。
- 不变量：**插件收尾与总线关闭均发生在 ``_shutdown_event.set()`` 之前**——
  ``await runtime`` 解除阻塞时所有善后已完成。
- **无生命周期状态机**（M-77 最终裁决，与 Agent 侧同裁）：Runtime 不提供
  ``status`` 字段、不区分 ``initializing/running/shutting_down/stopped``
  中间态。上表是**调用时序约定**而非状态机——框架不跟踪、不校验当前处于
  哪个"阶段"，乱序调用的后果由被调方法的契约各自承担（如 mount 前未
  ``use`` 的插件就是没装上）。「是否已关闭」的观测语义由 ``shutdown()``
  返回与 ``await runtime`` 解除阻塞表达，无需投影字段。

**3. 创建管线与恢复管线（逐阶段不变量）**

``create_agent`` 与 ``recover_agent`` 是**两个函数**，共用同一管线结构，
差异仅收窄为两处：① ``node_id`` 来源（新 UUID vs 已有 id，身份连续）；
② 恢复管线多一步 ``instance._restore()``（双袋重放，**D2 重排**：在
``before_recover`` 之前完成——重放是读/加载，放钩子前无碍；setup 是写，
必须在钩子后：阻断时 setup 未跑、无新写入，一致）。**禁止合并**成带
``resume`` flag 的单函数。两条管线在各自的新实例上各跑一次 ``setup()``
（recover 无独立 ``recover()`` 方法），故 ``setup()`` 必须可重入（见
``flowing.agent.Agent.setup``）。持久化状态的声明（``Agent.register_state``）
发生在 setup 中——恢复管线重放**先于** setup 完成（无 schema 装袋，声明
后 default 读出回退，不冲突）。

.. code-block:: text

    create_agent（新建管线，顺序为不变量）：
      get_agent_class(agent_type)      # 类型名 → 类（惰性解析）
      → __new__ + node_id / runtime / _parent_id 绑定
      → __init__（同步骨架，含 _open_stores 建立持久化后端与 _extra，P3-03）
      → 身份四键整写 meta.json（D12：agent_type / parent_agent_id /
        created_at / args，JSON 整写、非状态、Runtime 属主；before_create
        对 kwargs 的改写不落盘——恢复时经 before_recover 重新表达）
      → before_create 钩子（create 管线专属——recover 不触发；可改写 kwargs）
      → setup(**kwargs)
      → PENDING 检查（固定步骤，非钩子）
      → 池注册（core 名录 + _agent_pool 内存条目）
      → _nodes 注册（创建即注册，结构保证「不可能创建而不注册」）
      → after_create 钩子
      → 常驻工作循环 Task 启动

    recover_agent（恢复管线）：
      读池元数据 meta = _agent_pool[agent_id]
      → get_agent_class(meta["agent_type"])
      → args = 持久化 args，override_args 覆盖（override 覆盖持久化值）
      → __new__ + node_id = agent_id（已有 id，身份连续）
      → __init__（同步骨架，含 _open_stores，P3-03）
      → instance._restore()（只有 recover 有这一步；Agent 双袋重放
        core.jsonl / state.jsonl——D2 前移到 before_recover 之前）
      → before_recover（可改写 args）
      → setup(**args)（触发 before/after_recover 钩子对，
        **不再触发** before_create/after_create；setup 写 state 不再被
        重放覆盖）
      → PENDING 检查
      → _nodes 注册
      → after_recover
      → 常驻工作循环 Task 启动

- 两条钩子链**不混用**：父 Agent 的 hooks 管「唤起」
  （``before_subagent_invoke`` / ``after_subagent_invoke``）；
  子实例的 hooks 管「创建/生命周期」（``before_create`` / ``after_create`` /
  ``before_recover`` / ``after_recover``）。
- PENDING 检查是管线固定步骤（``setup()`` 之后、after_* 之前），
  检查 ``setup()`` 承诺的延迟定义（``PENDING`` 哨兵）是否已兑现；详见
  ``flowing.parsable.PENDING`` 与 ``flowing.agent.Agent.setup``。

**4. 恢复语义（消息级，文档 14 落锤）**

- ``Agent._restore()`` 从自己 session 目录的 ``tree.jsonl`` 逐行重建**消息级树**
  （``Message.id`` + ``parent_id`` 链）；崩溃造成的撕裂末行直接丢弃。
- 恢复边界 = **已持久化消息**（消息完整后 append 是唯一持久化时机）；
  进行中的逻辑 Turn 不恢复、不续跑（不做「半截 Turn 精确续跑」）。
- 半截 turn 的消息保留在树上、照常进入 LLM 上下文（M-28 裁决：不做
  turn_end 边界截断，仅丢弃未完整 append 的半截消息）；孤立 tool_call
  合成 ``synthetic=True`` 的 tool_result 占位封闭成对（详见
  ``flowing.message`` 与 ``flowing.agent`` 规约）。
- 恢复后不变量：已持久化消息全部在（可继续对话）；``current_head_id`` 指向
  消息树中最后持久化的消息；队列中待消费消息在（用户输入不丢，恢复后作为新
  逻辑 Turn 处理）。
- 主 agent 恢复**不递归**子 agent：恢复代价与树大小无关；子 agent 在继续被调用时
  经「有 key 无 value → 现场恢复」统一语义重建。

**5. 依赖校验（``use()`` 时增量校验，与 mount 解绑）**

- 插件只声明 ``dependencies: list[str]``（R4），检查是框架职责，但与 mount
  **不绑定**（R9 裁决：mount 只是根节点挂载，不承担校验职能）。
- 校验时点：每次 ``use()`` 安装后，对**当前已装集合**的子图增量校验：
  **成环** → 抛 ``DependencyError``（报错现场即引入环的那次 ``use()``，
  分批安装不受影响——缺依赖不报错，只有真成环才报）；**依赖缺失** →
  ``warnings.warn`` 警告不抛（「声明了依赖但实际用不上」是合法形态，
  可用 ``-W error`` 升级回错误）。
- ``use()`` 仍按实参顺序执行 ``install()``，但顺序**不再**是依赖正确性的保障。
- 运行时动态场景的兜底：inject 失败 → ``MissingProvideError``。

**6. 配置优先级链与浅合并**

- 优先级（高 → 低）：**环境变量 > 命令行参数 > 用户级（``$FLOWING_CONFIG_HOME``，
  默认 ``~/.flowing/``）> 项目级（``@/config.yaml``）> 框架推荐默认值**。
- 合并策略 = **浅合并**：高优先级覆盖低优先级同名字段；未出现的 key 沿用低优先级
  值（不是整块替换）；列表值**整列表覆盖**（不做列表合并）。
- 适用范围：仅数值类/字符串类配置；框架行为（钩子系统、Turn 循环流程、Tool 执行
  模型）是代码，不在此链上；Provider 三件套（providers/models/model-tags.yaml）
  有各自独立的优先级规则（见 ``flowing.model``）。
- 共存与透传：框架只解析自己认识的命名空间（``agent`` / ``runtime``）；其余 key
  透传给经 ``register_config_namespace`` 声明的扩展；**未注册的命名空间静默保留**，
  可经 ``get_config()`` 自由读取；多扩展注册同一命名空间 →
  ``ConfigNamespaceConflictError``。
- 环境变量表：``FLOWING_CONFIG_HOME`` / ``FLOWING_LOCALE`` /
  ``FLOWING_PROVIDERS_PATH`` / ``FLOWING_MODELS_PATH`` / ``FLOWING_MODEL_TAGS`` /
  ``FLOWING_PROVIDER_MODULES``（旧 ``FLOWING_TAG_MAPPING_PATH`` 已废弃）。
- 核心配置命名空间已知 key 与默认值：``agent.timeout``（60）、``agent.max_turns``
  （20）、``agent.max_depth``（10）、``runtime.log_level``（info）。

**7. 路径前缀规则（``resolve_path`` 的语义表）**

.. code-block:: text

    @/     → project_root（项目根目录）
    ./     → source_dir（未提供 source_dir 则报错）
    ../    → source_dir.parent；多级 ../../ 逐级向上
    含 / 或反斜杠字符且无 :: 的相对路径
           → 以 source_dir 为基准解析（未提供 source_dir 则报错）
    裸名   → 不涉及路径解析，走名称查找（注册表视图 default:: / builtin::，见 §7a）
    绝对路径 → 原样接受；对象表示：根内经 to_project_path 归一化为 @/ 形式，根外保留绝对路径

- 适用面：``$`` 引用、``{% include %}``、``subagents:`` / ``tools:`` / ``skills:``
  等**所有**文件路径引用。
- ``source_dir`` 默认取 ``Agent.source_dir`` 属性（``source_file`` 所在
  目录的换算唯一承担者，见 ``flowing.agent``，P3-11 裁决）。
- **无默认扫描目录**：项目资源引用一律显式 glob 路径（``./subagents/*``、
  ``@/tools/*``）。三类资源的查找根一律是引用方 Agent 定义文件所在目录
  本身（**不设** ``skills/`` 之类的默认子目录约定）。

**7a. 资源命名空间（Agent 类型 / 工具 / 技能的身份标识）**

每个已注册/已解析资源的完整身份是 ``<命名空间>::<规范名>``。命名空间
只参与注册、解析与去重，**从不进入 LLM 可见面**（渲染/调用只暴露规范名
或别名）。三个来源：

- **插件注册**：``register_tool`` / ``register_agent_type`` 与插件注入的
  ``register_skill``（SkillPlugin install 时绑定，见
  ``flowing.plugins``「绑函数约定」）可带 ``namespace`` 参数；缺省落入
  ``default::``。
  核心自带资源隐式归属 ``builtin::``（引用时**不需要**写出前缀）。
- **裸名引用的注册表视图**：裸名 = 依次查 ``default::``、``builtin::``
  （``default`` 优先——这是**插件覆盖原生行为的正式通道**，与「用户配置
  压系统默认」同构；被覆盖的 builtin 仍可用 ``builtin::name`` 显式引用）。
  自定义命名空间的资源必须写全限定名 ``ns::name`` 引用，且**只查注册表**、
  不走文件查找链。
- **文件资源（注册表未命中的裸名/路径引用）**：命名空间从所在位置派生——
  取资源**所在目录**（文件夹式资源取其上层目录）；目录在 ``@/``（项目根）
  以下用自 ``@`` 起的相对路径（如 ``@/subagents``），在 ``@/`` 以外用
  绝对目录路径。该命名空间是**内部身份标识**（去重/冲突检测用），引用处
  写法不变（裸名/路径照旧）。
- **冲突语义分两层**：注册表层，``ns::name`` 全键重复 → 后注册者报错；
  不同命名空间的同名资源可以共存。Agent 绑定层是 **LLM 视角**——LLM
  看不到命名空间（有别名时连规范名也看不到），同一 Agent 引用两个同名
  资源时必须给其一**起别名**，否则抛
  :class:`flowing.errors.EntryNameConflictError`；不同 Agent 各引各的
  同名资源互不影响。
- 目录形态 Agent 的入口文件按序探测、**首个存在者生效**：``AGENT.fya`` >
  ``agent.fya`` > ``<name>.agent.fya`` > ``<name>.fya``（目录存在但无任一
  候选 → 解析失败）；同名 Agent 的单文件（``payment.fya``）与文件夹并存时
  **文件夹优先**；引用方不区分两种形态（都写 ``payment``），单文件可无缝
  升级为文件夹。身份名 / class_name 推断规则见 :class:`flowing.agent.Agent`。
  注意：该顺序只是**确定性裁决规则**——不推荐同一链路上真的同时存在多个
  候选文件（属组织异味：读者需回溯优先级才能确定生效者）。
- 不引入 ``ProjectPath`` 类型——路径解析就是字符串前缀判断。

**8. 全局持久化状态（Runtime/插件级）**

与 per-agent 状态同构但**不挂在任何 agent 名下**：持久化根目录下
**每个命名空间一个文件**（``<persist_root>/<namespace>.jsonl``），与
per-agent session 目录并列。声明与访问经 ``Runtime.register_state`` /
``Runtime.state``（返回同一个 :class:`flowing.persistence.StateView` 类，
写透 / 末行合并 / 压缩 / defaults / fail fast 契约全部继承）。``core``
全局命名空间保留给框架，内容至少含**已注册 agent id 名录**（及平行映射
``session_dirs``：agent_id → session 目录存储形式，根内相对 / 根外绝对）与
已安装插件清单。引导时序：``set_persist_dir`` 之后、池扫描 / 首个 ``mount()``
之前重放全部全局命名空间。**加载/派生时机由插件内部管理**
（最终裁决）——核心不提供 ``load`` 恢复回调；重放产物就位后，插件
何时读出持久值重建运行时结构是插件自己的事（懒重建、显式初始化方法
均可）。

**9. agent 池懒加载与现场恢复**

- 池结构：``{agent_id → {agent_type, parent_agent_id, created_at,
  session_dir, args}}``（key + 元数据持久化，**实例不在扫描阶段创建**；
  ``session_dir`` 为该 agent 的 session 目录存储形式——根内相对 / 根外
  绝对，缺省 ``persist_dir / agent_id`` 的等价形式，恢复与扫描据此定位）。
- **名录为准**（最终裁决）：池 key 的唯一权威来源是全局 ``core`` 命名
  空间中的**已注册 agent id 名录**（``runtime.state("core")["agents"]``，
  写透持久化）；目录扫描只是按名录逐个开 session 目录。注册 agent =
  一次 core 状态写入（天然持久）；「显式删除才移除池 key」= 删 session
  目录 + 删名录项的一次操作。名录中 id 对应目录缺失 → 可诊断错误
  （不按空 session 静默处理）。
- ``agent_id`` 可重现三机制（缺一不可）：① 池 key = 名录项（=
  session 目录名）；② 元数据含 ``agent_type``（字符串类型名，跨进程
  稳定）；③ ``destroy()`` 不删记录（只丢实例，session 与名录项保留到
  显式删除）。
- **归档通道（``Runtime.archive_agent``）**：从 ``_nodes`` / ``_agent_pool`` /
  ``core`` 名录一并移除整棵子树，**保留 session 文件**——运行时完全遗忘、
  文件留档（归档 ≠ destroy ≠ 删文件；归档后 ``get_agent`` 返回 ``None``、
  ``recover_agent`` 抛 ``KeyError``——**归档后的 id 以池为准：
  池中无即不可恢复，报错**）。名录无 id + 目录存在 = **归档留档态**，
  不报错、不可自动恢复；**但显式 ``create`` 撞留档目录时报错**
  （``FileExistsError``，防止两份历史混杂，见 ``create_agent`` 管线
  第 2 步）——重新引入需外部运维把 id 加回名录。
  节点可为 Agent 或 Workflow（二者都注册 ``_nodes``、都有 ``_parent_id``
  与 ``destroy()``，归档不区分类型；收集走池 ``parent_agent_id`` 链 +
  ``_nodes`` ``_parent_id`` 链双来源）。父节点不持久化（如 Workflow 崩溃）
  留下的 parent 悬空条目，经 ``Runtime.archive_orphans`` 枚举逐个递归归档
  清理。
- 统一语义：**「有 key 无 value → 现场恢复」**，覆盖两场景：主 agent 恢复后
  未实例化的子 agent、匿名子 agent ``destroy()`` 后（session 保留）——两者走同一条
  ``recover_agent`` 路径，恢复后上下文延续（「同一个子 agent」有记忆）。
- 池空闲回收策略初版不实现，仅约定为后续版本候选。

**10. 懒加载总原则（Provider 与 agent 池同构）**

- Provider：Runtime 初始化仅扫描（providers.yaml 条目 + ``FLOWING_PROVIDER_MODULES``
  注册的 adapter 类）构建候选清单，**不实例化**；首次调用解析到某 provider 时才
  实例化并缓存，**一个条目一个实例**，对调用方透明（详见 ``flowing.model``）。
- agent 池：同上，扫描构建注册表，实例按需创建/现场恢复。
- 动机：启动成本与「实际用了多少」成正比。反模式：在 ``__init__`` 或 ``use()``
  里实例化所有 Provider / 所有 agent。

**11. 读取对称与调用时机约束**

- 配置/资源/设置的读取**全部是 Runtime 实例方法**，不做模块级全局函数；
  不存在「隐式拿到某个 Runtime」的旁路（TLS/contextvar 方案已否决）。
- ``get_config()`` 调用时机约束：只能在 ``setup()`` 和钩子回调中调用，
  **禁止模块顶层调用**（import 时优先级链合并未完成）→ 违反抛 ``ConfigNotReadyError``。
- ``get_config()`` 不做命名空间访问控制——任何代码可读任何命名空间；注册只是
  「谁负责校验」的声明，不是访问权限。

.. rubric:: 行为规约（模块级非行为清单）

- 不为「隐式拿到 Runtime」发明模块级全局函数或线程局部。
- 不在 Runtime 之外另设「对象图根」。
- 插件 ``install`` 里不查询其它插件（R2）、不做业务（R1）、运行时不增删全局注册
  状态（R3）——约定 R1–R4 为文档约定，非结构强制；依赖校验在 ``use()``
  时增量执行（R9 裁决，与 mount 解绑）。
- 不引入 ``flow.yaml`` 声明式入口（入口永远是 Python ``main()``）；CLI 只做
  「``launch`` 拿 Runtime」，不解析配置、不认识插件、不感知 ``@``（详见
  ``flowing.interfaces.cli``）。
- 信号处理（SIGINT/SIGTERM）→ ``shutdown()``：协作式关闭，不用 ``os._exit``
  （除非关闭本身卡死，那是应用层兜底，非框架职责）。

.. seealso::

    :mod:`flowing.agent` —— ``Agent`` 基类与创建/恢复管线的另一半（``setup`` /
    常驻工作循环）。
    :mod:`flowing.message` —— 消息级树字段（``parent_id`` / ``turn_end`` 等）。
    :mod:`flowing.errors` —— 本模块抛出的异常层次。
    :mod:`flowing.plugins` —— ``Plugin`` 基类与 R1–R4 约定。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import json
import logging
import os
import warnings

from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ruamel.yaml import YAML   # 与 flowing.model 同一 yaml 库选型（R-10 澄清）

from flowing.persistence import FileRecordStore, StateView
from flowing.errors import (
    ConfigNamespaceConflictError,
    ConfigNotReadyError,
    DependencyError,
    FormatError,
    MissingFieldError,
    MissingProvideError,
    NameMismatchError,
    ResourceNameConflictError,
    ResourceNotFoundError,
    UnknownHookPointError,
)
from flowing.params import ConfigKey, InjectionKey
from flowing.paths import NamingRules
from flowing.paths import classify_ref as _classify_ref
from flowing.paths import infer_name as _infer_name
from flowing.paths import kebab_to_snake as _kebab_to_snake
from flowing.paths import probe_candidates as _probe_candidates
from flowing.paths import resolve_path as _paths_resolve_path
from flowing.paths import to_project_path as _paths_to_project_path
from flowing.provide import ProvideNode, inject_from
from flowing.providers import ProviderRegistry, load_provider_candidates
from flowing.snapshot import AgentInfo, NodeInfo, RuntimeSnapshot
from flowing.builtins import register_builtins
from flowing.tool import Tool, ToolRegistry

if TYPE_CHECKING:
    from flowing.agent import Agent
    from flowing.plugins import Plugin
    from flowing.plugins.workflow import Workflow

__all__ = [
    "AGENT_NAMING",
    "ProvideNode",
    "Runtime",
    "inject_from",
    "launch",
    "resolve",
]

T = TypeVar("T")

_logger = logging.getLogger("flowing.runtime")

AGENT_NAMING = NamingRules(
    suffixes=(".agent.fya", ".fya", ".py"),
    generic_names=frozenset({"agent.fya", "AGENT.fya"}),
)
"""Agent 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻候选链声明（:meth:`Runtime.get_agent_class`）：命中通用名候选
（``agent.fya`` / ``AGENT.fya``）→ 身份名取目录名；否则文件名去首个
匹配后缀、snake→kebab。供 agent 装配层调
:func:`flowing.parser.parse_fya` 时传入 ``naming=AGENT_NAMING``，以及
name 断言的推断侧。
"""

_FRAMEWORK_CONFIG_DEFAULTS: dict[str, Any] = {
    "agent": {"timeout": 60, "max_turns": 20, "max_depth": 10},
    "runtime": {"log_level": "info"},
}
"""框架推荐默认值层（配置优先级链的最底层，模块 docstring §6 已知 key 清单）。
内部 API，不属稳定契约。
"""

_POOL_META_KEYS: tuple[str, ...] = (
    "agent_type", "parent_agent_id", "created_at", "args",
)
"""身份四键（D12）：create 管线第 3b 步整写进 agent 自己 session 目录的
``meta.json``（JSON 整写、非状态、Runtime 属主）；池扫描 / 跨进程恢复
经 ``_read_pool_meta`` 读回重建 ``_agent_pool`` 条目。内部 API，
不属稳定契约。
"""

_current_project_root: ContextVar[Path | None] = ContextVar(
    "_current_project_root", default=None)
"""``@`` 项目根上下文的载体（**内部 API，不属稳定契约**）。

功能与动机：``launch`` 用 ``.set(Path(path).resolve())`` 登记、``.reset(token)`` 复位；
模块级 ``resolve()`` 与 ``Runtime.__init__`` 读同一 contextvar，保证两条通道同源。
asyncio 中 per-Task 隔离，是多 Runtime 并发的时序稳定机制。

行为边界：未登记时取值为 ``None``；用户代码不应直接读写本变量。

.. seealso:: :func:`flowing.runtime.launch`、:func:`flowing.runtime.resolve`
"""


async def launch(
    path: str | Path,
    main_file: str | None = None,
    **kwargs: Any,
) -> Runtime:
    """Runtime 的**唯一创建入口**：登记 ``@`` 上下文 → import 子项目入口
    main 文件（缺省 ``@/main.py``，经 ``main_file`` 或 CLI ``-m`` 指定）
    → ``await mod.main(**kwargs)`` → 返回配置好的 Runtime。

    .. rubric:: 功能介绍

    框架核心层函数。把一个 flowing 子项目目录拉起为配置完毕的 Runtime：
    子项目的入口 main 文件（缺省 ``@/main.py``；``main_file`` 指定替代
    路径，CLI 上以 ``-m`` 传入，支持绝对路径与 ``@`` 相对路径）必须导出
    ``async def main(**kwargs) -> Runtime``，
    在其**内部**创建 Runtime（可任意子类，``@`` 自动绑定到实例）、``use``、
    ``provide``、``mount``，并返回它。CLI / HTTP / Web / 测试 / 嵌入五种暴露
    方式共用此入口——**调用方决定如何暴露 Runtime**，框架不耦合暴露方式。

    .. rubric:: 设计动机

    - 「唯一入口」保证 ``@`` 上下文必有登记点：绕过 ``launch`` 直接 ``Runtime()``
      会因 contextvar 为空而抛错，从机制上消灭「``@`` 无来源」的反模式。
    - contextvar per-Task 隔离替代旧「进程单例」方案：一个进程可有多个 Runtime，
      并发 ``launch`` 互不串扰；强隔离需求仍归子进程。
    - 不引入 ``flow.yaml`` 声明式入口：三行 Python 的 ``main()`` 已覆盖最简项目，
      避免两套解析逻辑。

    .. rubric:: 使用示例

    .. code-block:: python

        # @/main.py —— 子项目入口（launch 会 import 并调用它）
        from flowing import Runtime

        async def main(workspace_root: str = "/ws") -> Runtime:
            runtime = Runtime()                      # @ 自动绑定到实例
            runtime.provide("workspace_root", workspace_root)
            await runtime.mount("@/root.fya")
            return runtime

    .. code-block:: python

        # 嵌入方 / CLI 内部
        runtime = await launch("/path/to/flowing-project", workspace_root="/ws")
        await runtime                                # 进程存活，直到 shutdown()

    .. rubric:: 行为规约

    - 时序：``_current_project_root.set(Path(path).resolve())`` → ``import
      <path>/main.py`` → ``await mod.main(**kwargs)`` → ``finally: reset(token)``。
    - ``resolve()`` 在登记后**立即可用**——``main()`` 内 ``Runtime()`` 构造之前
      也能 ``flowing.resolve("@/config.yaml")``。
    - ``reset`` 发生在 ``main()`` 返回之后；spawn 的子 Task 已继承正确值，不受影响。
    - ``**kwargs`` 原样透传给 ``main``；CLI 的 ``--key value`` 规则（``-`` → ``_``、
      无值为 ``True``、value 原样字符串）由 CLI 层负责（见 ``flowing.interfaces.cli``），
      本函数不做参数变换。
    - 返回值是 ``main()`` 返回的 Runtime——此时 Agent 已激活、工作循环 Task 已就绪；
      若调用方不做 ``await runtime``，进程/任务随即无事可做。
    - 非行为：本函数不解析配置、不认识插件、不决定「新建 vs 恢复」（那是
      ``main()`` 的策略）、不启动任何服务端口。

    :param path: flowing 子项目目录（普通文件系统路径，CLI 层不感知 ``@``）。
    :param kwargs: 透传给子项目 ``main(**kwargs)`` 的任意参数。
    :return: 配置完毕的 ``Runtime``。
    :raises RuntimeError: 子项目缺少 ``main.py`` 或 ``main`` 签名不符约定时
        （import/调用失败的原生异常直接上抛，框架不包装）。

    .. rubric:: 测试案例

    - 前置：子项目含合法 ``main.py`` → 操作：``await launch(path)`` → 期望：
      返回 Runtime；``runtime.project_root == Path(path).resolve()``。
    - 前置：两个并发 Task 各自 ``launch`` 不同子项目 → 期望：两个 Runtime 的
      ``project_root`` 各自正确，互不串扰。
    - 前置：``main()`` 中 ``Runtime()`` 之前调用 ``resolve("@/x")`` → 期望：
      正常解析（登记先于 ``main`` 调用）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime._current_project_root`` 的 ``set()`` / ``reset()``（时机：登记 ``@`` 上下文与 ``main()`` 返回后复位，时序见本函数行为规约）；子项目 ``main(**kwargs)``（用户代码，每次 launch import 后 await）
    - 被调：``flowing.interfaces.run.cmd_run`` / ``flowing.interfaces.repl.cmd_repl`` / ``flowing.interfaces.serve.cmd_serve`` / ``flowing.interfaces.run.cmd_test``（各子命令第 1 步拉起 Runtime，见各 docstring）；``flowing.interfaces.web.cmd_web``（与 ``cmd_serve`` 共享拉起骨架）；``flowing.interfaces.cli.cmd_compile`` 明确不经 launch

    .. seealso:: :func:`flowing.runtime.resolve`、:class:`flowing.runtime.Runtime`、
        :meth:`flowing.runtime.Runtime.mount`
    """
    import importlib.util

    token = _current_project_root.set(Path(path).resolve())   # 时序第 1 步：登记 @ 上下文
    try:
        # 时序第 2 步：import 子项目入口 main 文件（缺省 @/main.py，main_file 支持
        # 绝对路径与 @ 相对路径）；原生 import 异常直接上抛，框架不包装
        main_path = resolve(main_file) if main_file is not None else Path(path).resolve() / "main.py"
        spec = importlib.util.spec_from_file_location("flowing_subproject_main", main_path)
        mod = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(mod)   # type: ignore[union-attr]
        runtime: Runtime = await mod.main(**kwargs)   # 时序第 3 步：**kwargs 原样透传，不做参数变换
        return runtime
    finally:
        _current_project_root.reset(token)   # 时序第 4 步：main() 返回后复位；spawn 的子 Task 已继承正确值

def resolve(path: str) -> Path:
    """模块级 ``@/`` 路径解析：读 ``_current_project_root`` contextvar。

    .. rubric:: 功能介绍

    框架核心层函数。把 ``@/`` 前缀路径解析为当前 Task 的 ``@`` 项目根下的绝对
    ``Path``。``launch`` 登记上下文后任何位置可用（包括 ``Runtime()`` 构造之前）。

    .. rubric:: 设计动机

    与 ``Runtime.__init__`` 读同一 contextvar，保证「``@`` 两条通道同源」；
    模块级函数形态使 ``main()`` 内加载配置文件等早期代码无需先持有 Runtime。

    .. rubric:: 使用示例

    .. code-block:: python

        import flowing

        async def main() -> Runtime:
            config = load_yaml(flowing.resolve("@/config.yaml"))   # Runtime() 之前也行
            runtime = Runtime()
            ...

    .. rubric:: 行为规约

    - ``path`` 以 ``@/`` 开头：返回 ``project_root / path[2:]``。
    - 非 ``@/`` 路径：不经 ``@`` 上下文处理，按普通 ``Path`` 语义返回
      （相对路径基于当前工作目录）；完整的前缀规则（``./`` / ``../`` / 裸名）
      是 :meth:`Runtime.resolve_path` 的职责，本函数只负责 ``@/``。
    - 未经 ``launch`` 登记上下文（contextvar 为空）时抛错，不留静默回退。

    :param path: 待解析路径字符串。
    :return: 解析后的 ``Path``。
    :raises RuntimeError: 未经 ``flowing.launch`` 登记 ``@`` 上下文时抛出
        （消息指明必须经 ``launch``）。

    .. rubric:: 测试案例

    - 前置：``launch`` 进行中 → 操作：``resolve("@/config.yaml")`` → 期望：
      ``<path>/config.yaml``。
    - 前置：无任何 ``launch`` → 操作：``resolve("@/x")`` → 期望：``RuntimeError``。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime._current_project_root`` 的 ``get()``（时机：每次调用读 contextvar）
    - 被调：无（框架内未见调用点；docstring 示例为用户 ``main()`` 代码）

    .. seealso:: :func:`flowing.runtime.launch`、
        :meth:`flowing.runtime.Runtime.resolve_path`
    """
    root = _current_project_root.get()
    if root is None:
        raise RuntimeError("未经 flowing.launch 登记 @ 上下文，resolve() 不可用")
    if path.startswith("@/"):
        return root / path[2:]
    return Path(path)   # 非 @/ 路径：普通 Path 语义（完整前缀规则是 Runtime.resolve_path 的职责）


def _check_pending(instance: "Agent", agent_type: str) -> None:
    """创建/恢复管线第 6 步的 PENDING 检查器（R-05 落实；**内部 API**）。

    扫实例 ``__dict__`` 与类 MRO 属性中的 ``PENDING`` 哨兵（含
    ``Parsable`` 包裹形态——``Parsable.source is PENDING``），命中即抛
    :class:`flowing.errors.MissingFieldError`（该异常为单字段结构
    （``field`` / ``agent_type``），多字段命中时报**首个**（定义序），
    其余待修复后下次创建再报——fail fast 逐钉语义）；
    同帧结算 S-29 扩展：``instance.hooks._pending_on`` 非空（``@on``
    暂记的钩子点在 setup 结束前未被 declare）→ 抛
    :class:`flowing.errors.UnknownHookPointError`（消息列出 hook_name
    与方法名）。

    .. rubric:: 调用关系（审计）

    - 调用：无具名符号（读实例 ``__dict__`` / 类 MRO / ``hooks._pending_on``）
    - 被调：``Runtime.create_agent`` / ``Runtime.recover_agent``（各自管线
      第 6 步，``setup()`` 之后、after_* 钩子之前）
    """
    from flowing.parsable import PENDING, Parsable   # 局部 import：模块头依赖图保持单向

    for mapping in (vars(instance), *(vars(c) for c in type(instance).__mro__)):
        for field, value in mapping.items():
            if field.startswith("__"):
                continue
            if isinstance(value, Parsable):
                value = value.source   # Parsable 包裹形态：哨兵在 source 位
            if value is PENDING:
                raise MissingFieldError(field, agent_type)
    # S-29 扩展：同帧结算 @on 暂记（目标钩子点未 declare 即未消费）
    if instance.hooks._pending_on:
        unclaimed = [f"{hook_name}（方法 {getattr(bound, '__name__', bound)}）"
                     for bound, hook_name, _, _, _ in instance.hooks._pending_on]
        raise UnknownHookPointError(
            f"@on 标记未找到归属钩子点：{'; '.join(unclaimed)}"
            "——钩子点名拼写错误，或对应插件/Composable 未在 setup() 中启用")


# ``ProvideNode`` 协议与 ``inject_from`` 统一上溯算法的 canonical home 是
# ``flowing.provide``（S-43 裁决③：共享符号下沉叶子模块，破 agent↔runtime
# 循环依赖）；此处为 import + 再导出（``__all__`` 保留），旧引用
# ``flowing.runtime.ProvideNode`` / ``flowing.runtime.inject_from`` 保持有效。


class Runtime:
    """子项目实例、对象图根与唯一全局容器。

    .. rubric:: 功能介绍

    一个 ``Runtime`` = 一个运行中的 flowing 子项目：持有全部 Agent
    实体（``_nodes``）、已安装插件（``_plugins``）、全局工具/子智能体
    注册表，并提供扩展 API（``register_tool`` / ``provide`` 等）与观测
    通道（``snapshot``）。持久化状态的**声明与访问在 Agent 侧**
    （``Agent.register_state`` 声明 / ``Agent.state`` 显式视图读写——
    单袋、无命名空间，P3-03 配套裁决）；Runtime/插件的**全局**状态保留命名空间
    （``Runtime.register_state`` / ``Runtime.state(ns)``），其加载与
    派生时机由插件内部管理（核心只提供声明/写透/引导重放）。
    Runtime 另管全局目录、池扫描与管线编排。

    架构性契约（生命周期两段式、创建/恢复两条管线的逐阶段不变量、
    恢复语义、关闭语义）统一维护在**模块 docstring**，此处不重复展开，
    避免双写漂移。

    .. rubric:: 行为规约

    - 创建即注册：Agent 诞生必经 ``_nodes`` 注册（结构保证「不可能
      创建而不注册」）。**三档遗忘强度**：
      **destroy**（``Agent.destroy()``）= 丢实例、从 ``_nodes`` 摘除，
      池 key 与名录保留——可经 ``recover_agent`` / ``get_agent`` 现场
      恢复；**archive**（:meth:`archive_agent`）= ``_nodes`` / 池 /
      ``core`` 名录一并移除整棵子树，session 文件留档——运行时完全
      遗忘（``get_agent`` 返回 ``None``），重新引入需外部运维把 id
      加回名录；**删除** = 物理删除 session 目录 + 名录项，不可逆——
      **框架不提供删除功能**，应用层需要删除已有归档文件请自行实现
      （建议先归档再删除：先经 ``archive_agent`` 收编运行时引用，
      再删文件，避免活引用指向已消失的目录）。
    - ``shutdown()`` 的返回与 ``await runtime`` 解除阻塞表达「已关闭」
      的观测语义——无生命周期状态投影字段。

    .. rubric:: 调用关系（审计）

    - 调用：无（类声明层）
    - 被调：无（类声明层；成员方法的被调关系见各方法 rubric）
    - 实例化方：无框架内实例化点——由子项目 ``main()`` 在 ``flowing.runtime.launch`` 登记的 ``@`` 上下文内构造（示例见 ``flowing.interfaces.run.cmd_run`` docstring）；绕过 launch 构造抛 ``RuntimeError``

    .. seealso:: :class:`flowing.agent.Agent`、
       :meth:`flowing.agent.Agent.register_state`
    """

    _nodes: dict[str, ProvideNode]
    """活体表（node_id → 节点实体；Agent / Workflow 均注册在内，
    spec-draft 02 §5.2 裁决）。内部 API，不属稳定契约。
    """
    _plugins: dict[str, Any]
    """已安装插件表（插件 name → 实例），``get_plugin`` 的查询源。
    内部 API，不属稳定契约。
    """
    node_id: str
    """固定为 ``"runtime-0"``（共享 ID 空间的 ``runtime-`` 前缀；单 Runtime 的
    共享 ID 空间内唯一——一个 Runtime 下不可能有多个 Runtime）；本 Runtime 是
    inject 链终点与 ``_nodes`` 中所有节点的最终父级，``__init__`` 时自注册为
    ``_nodes`` 首条目。参见 :class:`ProvideNode`。
    """
    runtime: "Runtime"
    """:class:`ProvideNode` 协议成员：链终点的 ``runtime`` 自指（同 Vue 根组件
    ``this.$root === this`` 语义）；``__init__`` 时真实赋值——仅注解不赋值
    方案被否决：类级 ``runtime: "Runtime"`` 只是类型注解，**不产生实例
    属性**（注解不进实例 ``__dict__``）；Python 3.12+ 的 ``isinstance``
    对 ``runtime_checkable`` 协议也用 ``hasattr`` 判数据成员，缺赋值会使
    ``isinstance(runtime, ProvideNode)`` 为假——协议检查假阴性，故必须
    在 ``__init__`` 里 ``self.runtime = self`` 真实赋值。
    """
    project_root: Path
    """``@`` 上下文的固化值：``__init__`` 时从 ``_current_project_root`` 读取；
    ``resolve_path("@/...")`` 的解析基准。只读语义，构造后不改。
    """
    tool_registry: ToolRegistry
    """全局工具注册表（规范名 → ``Tool``）；``register_tool`` 的写入目标；
    读取经 Agent 的 ``tool_call`` 按别名查 ``_tool_entries``（见 ``flowing.tool``）。
    """
    provider_registry: ProviderRegistry
    """Provider 懒实例化表（providers.yaml 条目名 → ``Provider`` 实例；
    C-04 裁决补声明）。``Agent.provider_gen()`` 的 provider 懒获取入口；
    候选清单扫描完成后构造。adapter **类**的进程级注册表是
    ``flowing.providers.provider._provider_adapters``，与本表（条目实例）分层。
    """
    _provided: dict[str, Any]
    """根级 provide 存储，inject 链终点；敏感信息（API key / 凭证）禁止进入。
    内部 API，不属稳定契约。
    """
    _config_overrides: dict[str, Any]
    """运行期配置覆盖层（``set_config`` 的写入目标）：``get_config`` 读取时
    **先于**优先级链合并结果命中——「下游产品运行期复写框架配置」的通道
    （如 ``set_config("agent.timeout", ...)``）。**不持久化**（进程级，
    重启即失效；持久覆盖请改配置文件）。内部 API，不属稳定契约。
    """
    _config_namespaces: dict[str, Any]
    """配置命名空间注册表（命名空间 → 扩展声明的 schema）；注册只是「谁负责校验」
    的声明，不构成访问控制。内部 API，不属稳定契约。
    """
    _resources: dict[str, Any]
    """Resource 注册存储（name → 任意实例）；生命周期跨 Agent，独立于 provide 链。
    内部 API，不属稳定契约。
    """
    _agent_pool: dict[str, dict[str, Any]]
    """agent 池注册表：``agent_id → {agent_type, parent_agent_id, created_at, args}``；
    池 key 的唯一权威来源是全局 ``core`` 名录（见模块 docstring §8-9），
    **value 是元数据不是实例**。内部 API，不属稳定契约。
    """
    _agent_types: dict[str, type[Agent]]
    """子 Agent 类型注册表（``ns::注册名`` → Agent 类；裸名视图 =
    ``default::`` / ``builtin::``，见模块 docstring §7a）；
    ``register_agent_type`` 的写入目标，``get_agent_class`` 的查找源
    （S-37 补声明）。内部 API，不属稳定契约。
    """
    _states: dict[str, StateView]
    """全局持久化状态命名空间表（命名空间 → ``StateView``）；
    ``register_state`` 写入、``state(ns)`` 读取（S-37 补声明）。
    内部 API，不属稳定契约。
    """
    _persist_dir: Path
    """持久化根目录；默认 ``<cwd>/.flowing``（S-37 裁决），
    ``set_persist_dir`` 覆写（``mount()`` 前）。内部 API，不属稳定契约。
    """
    _model_tags_path: Path | None
    """模型标签文件路径（默认 ``~/.flowing/model-tags.yaml``，``FLOWING_MODEL_TAGS``
    环境变量优先；``set_model_tags`` 可编程覆盖）。**默认启用**——模型标签是
    模型解析的常规通道，无默认文件时按"未定义标签回退 default、default 也缺报错"
    处理（见 ``flowing.model``）。内部 API，不属稳定契约。
    """
    _models_path: Path | None
    """models.yaml 来源路径（``set_models`` 赋值；构造期解析为
    ``FLOWING_MODELS_PATH`` / ``$FLOWING_CONFIG_HOME/models.yaml`` 的有效
    默认路径——``Agent._resolve_model_tag`` 要求两路径均非 ``None``，
    「未设定 → 默认路径」的现场求值由构造期的就地换算承担）。
    内部 API，不属稳定契约。
    """
    _providers_path: Path | None
    """providers.yaml 来源路径（``set_providers`` 赋值；默认 ``None`` =
    构造期解析的 ``FLOWING_PROVIDERS_PATH`` / 默认路径）。内部 API，不属
    稳定契约。
    """
    _shutdown_event: asyncio.Event
    """退出事件；``__await__`` 等待它，``shutdown()`` 末尾置位。
    内部 API，不属稳定契约。
    """
    _config_ready: bool
    """配置就绪闸（R-06 落实）：优先级链浅合并在 ``__init__`` 尾部同步
    完成前为 ``False``，``get_config`` 未就绪即抛 ``ConfigNotReadyError``。
    内部 API，不属稳定契约。
    """
    _merged_config: dict[str, Any]
    """优先级链浅合并产物（点分扁平 key → 值）；``get_config`` 的第二读源
    （第一是 ``_config_overrides``）。内部 API，不属稳定契约。
    """
    env: MappingProxyType
    """``os.environ`` 只读视图——Parsable 渲染上下文的 ``env`` 入口
    （``{{ env.X }}`` 经 Jinja 的 attr→item 回退命中，R-4 澄清：env 直接
    以可点号引用对象进渲染上下文）。构造期建立，随进程环境快照语义
    （``os.environ`` 的活视图）。
    """

    def __init__(self) -> None:
        """构造 Runtime（同步）；``@`` 由 launch 上下文自动绑定到 ``project_root``。

        .. rubric:: 功能介绍

        必须在 ``flowing.launch`` 登记的 ``@`` 上下文内调用（通常由子项目
        ``@/main.py`` 的 ``main()`` 调用）。构造时固化 ``project_root``、初始化
        全部空存储、执行 Provider 候选清单与 agent 池的**扫描**（不实例化）。

        .. rubric:: 设计动机

        ``__init__`` 必须同步（``async`` 构造是反模式）；重量级初始化（Provider
        实例化、agent 实例化）全部推迟到首次使用（懒加载原则）。

        .. rubric:: 使用示例

        .. code-block:: python

            class MyRuntime(Runtime): ...          # 可任意子类化

            async def main() -> Runtime:
                runtime = MyRuntime()              # @ 自动绑定
                return runtime

        .. rubric:: 行为规约

        - 前置条件：``_current_project_root`` 已登记（即在 ``launch`` 的调用栈内）。
        - 构造期副作用仅限：固化 ``project_root``、初始化空存储、核心内置
          工具注册（``subagent-invoke`` / ``finish``，S-01/S-02 裁决）、扫描
          ``providers.yaml`` / ``FLOWING_PROVIDER_MODULES`` 构建候选清单、扫描
          持久化目录构建 agent 池注册表。**不实例化**任何 Provider 或 Agent。
        - 一进程可构造多个实例（各自 Task 的 contextvar 隔离），互不共享存储。
        - 非行为：不启动工作循环（没有 Agent 时没有 Task）、不打开端口、不读
          ``config.yaml`` 之外的项目文件。

        :raises RuntimeError: 未经 ``flowing.launch`` 登记 ``@`` 上下文（绕过唯一
            入口）时抛出。

        .. rubric:: 测试案例

        - 前置：launch 上下文内 → 操作：``Runtime()`` → 期望：成功且
          ``project_root`` 正确；provider 实例缓存为空（未实例化）。
        - 前置：裸 Python 进程无 launch → 期望：``RuntimeError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime._current_project_root`` 的 ``get()``（时机：构造时固化 ``project_root``，见行为规约前置条件）
        - 被调：无框架内调用方（由子项目 ``main()`` 构造；可任意子类化）

        .. seealso:: :func:`flowing.runtime.launch`
        """
        root = _current_project_root.get()
        if root is None:
            raise RuntimeError("必须经 flowing.launch 登记 @ 上下文（绕过唯一入口）")
        self.project_root = root   # 固化 @ 上下文（构造后不改）
        self.node_id = "runtime-0"
        self.runtime = self   # ProvideNode 协议成员：链终点的 runtime 自指
            # （同 Vue 根组件 this.$root === this）；仅注解不赋值被否决——
            # Python 3.12+ isinstance 对 runtime_checkable 协议也检查数据成员
        self._nodes = {self.node_id: self}   # 自注册为 _nodes 首条目——inject 链终点
            # 可达性的结构保证之一（另一根：根节点 _parent_id 指向本 id）；
            # shutdown 销毁循环须跳过自身（Runtime 无 destroy()）
        self._plugins = {}
        self.tool_registry = ToolRegistry()
        self._agent_types = {}   # 须在 register_builtins 之前初始化（ExploreAgent 注册写本表）
        # 框架自带工具与标准子智能体注册（P3-06：全部物理归
        # flowing.builtins）：核心内置 subagent-invoke / finish +
        # 六个标准文件/shell 工具 + ExploreAgent，随 Runtime 天生在场，
        # 归属 builtin:: 命名空间（裸名视图的兜底层，可被 default:: 覆盖，
        # 见模块 docstring §7a）；
        # 对 LLM 的可见性仍由 Agent 级 ``.fya`` ``tools:`` 声明或显式
        # ``add_tool`` 控制（注册 ≠ 可见，框架不静默附加——S-19）
        register_builtins(self)
        self._provided = {}
        self._config_overrides = {}
        self._config_namespaces = {}
        self._resources = {}
        self._agent_pool = {}
        self._states = {}
        self._bootstrapped: set[str] = set()   # 已重放命名空间集合（D5 删闸门后「未引导」判断的替代：_bootstrap_persistence 只重放未入集合的视图——防文件旧值覆盖未 drain 的内存写；阶段 D「注册即 replay」落地后退役）
        self._persist_dir = Path.cwd() / ".flowing"   # 默认持久化根（S-37 裁决）：本质是 flowing 启动路径——启动时可经 set_persist_dir 手动指定，默认为 cwd（R-07 澄清）；set_persist_dir 覆写（mount 前）
        config_home = Path(os.environ.get(
            "FLOWING_CONFIG_HOME", os.path.expanduser("~/.flowing")))
        self._model_tags_path = Path(os.environ.get(
            "FLOWING_MODEL_TAGS",
            str(config_home / "model-tags.yaml")))   # 默认启用（用户裁决：模型标签不可不启用；env 优先、默认 $FLOWING_CONFIG_HOME/model-tags.yaml，set_model_tags 可覆盖）
        self._models_path = Path(os.environ.get(
            "FLOWING_MODELS_PATH",
            str(config_home / "models.yaml")))   # 构造期就地换算有效默认路径（Agent._resolve_model_tag 要求非 None）
        self._shutdown_event = asyncio.Event()
        # 扫描 providers.yaml + FLOWING_PROVIDER_MODULES 构建 provider 候选清单
        # （S-03 裁决具名：load_provider_candidates；懒加载原则：不实例化任何
        # Provider，见模块 docstring §10）。路径解析：FLOWING_PROVIDERS_PATH
        # 环境变量优先，否则 $FLOWING_CONFIG_HOME/providers.yaml（默认
        # ~/.flowing/）；set_providers 可在 mount 前编程覆盖
        self._providers_path = Path(os.environ.get(
            "FLOWING_PROVIDERS_PATH",
            str(config_home / "providers.yaml")))
        self.provider_registry = ProviderRegistry(load_provider_candidates(self._providers_path))
        # R-06 落实：配置优先级链三层浅合并（框架默认 < 项目级 @/config.yaml
        # < 用户级 $FLOWING_CONFIG_HOME/config.yaml）在 __init__ 尾部同步完成，
        # 完成后置就绪闸；命令行层不进链（由调用方经 set_config 落
        # _config_overrides 表达），env 层暂无 key 映射规约（FLOWING_* 均为
        # 路径/开关类，由各自消费点直读）
        self._config_ready = False
        self._merged_config = self._merge_config_layers()
        self._config_ready = True
        self.env = MappingProxyType(os.environ)   # os.environ 只读视图（渲染上下文 env 入口，R-5）
        # R-07 落实：框架自登记 core 全局命名空间（已注册 agent id 名录 +
        # session_dirs 平行映射 + 已安装插件清单），随后引导重放（默认
        # persist 路径场景的重放点；set_persist_dir 会重指全部命名空间存储
        # 并再次引导——幂等）
        self.register_state(
            "core", defaults={"agents": [], "session_dirs": {}, "plugins": []})
        self._bootstrap_persistence(_materialize=False)   # 含 agent 池扫描（读 core 名录逐个开 session 目录；实例不在扫描阶段创建，见 §8-9）；构造期只读引导——不在 cwd 建默认目录（零持久化场景不污染工作目录，建目录归后续写意图时点，见 _bootstrap_persistence 的 _materialize 约定）

    def use(self, *plugins: Plugin) -> None:
        """安装插件（阶段一启用）：按实参顺序执行各插件的 ``install(runtime)``。

        .. rubric:: 功能介绍

        框架核心层方法，双层启用的第一层。插件在 ``install`` 中注册全局能力
        （工具 / provide 值 / 配置命名空间 / 状态回调），随后 Agent 在 ``setup()``
        中经 ``use_xxx(self)`` 做实例级启用（阶段二）；不调用的 Agent 零开销。

        .. rubric:: 设计动机

        - 内置扩展随包发布但**不自动启用**：未启用的扩展代码路径「从没存在过」，
          不是「被 skip」。
        - **可分批调用**：依赖校验随 ``use()`` 增量执行（R9：与 mount 解绑）——
          成环即抛 ``DependencyError``；缺依赖只警告（``warnings.warn``）不抛，
          分批安装不受影响（R4：依赖只声明，检查是框架职责）。
        - 实参顺序仍被尊重（按序执行 ``install``），但顺序**不再**是依赖正确性
          的保障。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.use(SkillPlugin())
            runtime.use(CommPlugin(), GuardrailPlugin())   # 分批合法

        .. rubric:: 行为规约

        - 时序约定（**非强制**，文档要求）：推荐在首个 ``mount()`` /
          ``create_agent()`` / ``recover_agent()`` 之前完成全部 ``use()``。
          框架不校验——之后 ``use()`` 不报错，但已创建的 Agent 不会
          retroactively 获得迟装插件注册的能力（R3「注册只在 install」
          是约定而非机制；依赖校验在每次 ``use()`` 后增量执行，见 R9）。
        - 同名 provide key：覆盖更新（由 ``provide`` 的覆盖语义承载，
          spec-draft §13.4——后者生效，inject 实时可见）。
        - 每次安装后调 ``_check_dependencies()`` 增量校验已装子图（成环抛错、
          缺失警告）；不实例化 Provider / Agent（R1 反模式：
          ``install`` 里 ``await runtime.create_agent(...)`` 属违规用法，框架不
          阻止但行为不受支持）。
        - 边缘情况：重复安装同名插件 → 后安装者报错（插件表 key 冲突）。

        :param plugins: 待安装插件实例，按顺序 install。
        :raises flowing.errors.ConfigNamespaceConflictError: 插件注册了已被占用的
            配置命名空间时（经 ``register_config_namespace`` 抛出）。

        .. rubric:: 测试案例

        - 前置：``use(A())`` 其中 A 声明依赖未安装的 ``"x"`` → 期望：
          ``warnings.warn`` 警告，不抛错；``use(B())``（A↔B 成环）→ 期望：
          ``DependencyError``。
        - 前置：两个插件注册同一配置命名空间 → 期望：
          ``ConfigNamespaceConflictError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.Plugin.install()``（时机：每次 ``use()`` 按实参顺序，每插件恰好一次，见 ``flowing.plugins`` 规约）
        - 被调：无框架内调用方（子项目 ``main()`` 阶段一安装；插件 install 示例见 ``flowing.plugins``）

        .. seealso:: :meth:`flowing.runtime.Runtime.mount`、
            :meth:`flowing.runtime.Runtime._check_dependencies`、
            :class:`flowing.plugins.Plugin`
        """
        for plugin in plugins:   # 按实参顺序执行 install；可分批调用
            if plugin.name in self._plugins:
                raise ValueError(
                    f"插件重复安装：{plugin.name}——同名插件已安装"
                    "（后安装者报错；spec 未具名异常类型，属编程错误，用内置 ValueError）")
            plugin.install(self)
            self._plugins[plugin.name] = plugin
        self._check_dependencies()   # 增量校验已装子图：成环抛 DependencyError；缺失 warnings.warn 不抛（R9：与 mount 解绑）
        # install 中声明的全局命名空间在安装完成后引导重放：插件在运行期
        # / shutdown() 中读写全局状态才可用
        self._bootstrap_persistence()

    def get_plugin(self, name: str, *, strict: bool = True) -> Any | None:
        """按名查询已安装插件实例；``strict`` 标志兼容「直接用」与「探测」两种写法。

        .. rubric:: 功能介绍

        返回 ``use()`` 安装过的插件实例（查询源：``_plugins``，key 为
        ``plugin.name``）。未安装时的行为由 ``strict`` 决定：

        - ``strict=True``（默认）：抛 ``KeyError``——直接用写法
          （``runtime.get_plugin("cron").jobs()``），未安装时立即以清晰
          异常失败，而非延迟到 ``None.jobs()`` 的 ``AttributeError``；
        - ``strict=False``：返回 ``None``——探测写法
          （``if runtime.get_plugin("skill", strict=False) is not None: ...``）。

        .. rubric:: 设计动机

        本方法的两种现存用法对「未安装」有相反诉求（C-01 裁决）：观测示例
        假定插件已装、要直接链式调用；``_unstable/logging`` 插件的**存在前提**
        是探测 skills/comm/cron/workflow 是否安装、未装属正常路径而非错误。
        单一形态只能偏袒一方；加 ``strict`` 旗标让两种写法各自成立，且不
        新增异常类型（复用 ``KeyError``，与 ``_plugins`` 的 dict 语义一致）。

        默认取 ``True``：**探测一定是有意主动触发的**——调用方明知自己在
        做存在性检查，显式写 ``strict=False`` 是意图的自我声明；而「直接用」
        是更常见的形态，应享受更短的写法与更响亮的失败。

        .. rubric:: 使用示例

        .. code-block:: python

            # 直接用（默认形态）
            jobs = runtime.get_plugin("cron").jobs()

            # 探测（显式关闭严格）
            if runtime.get_plugin("skill", strict=False) is not None:
                ...

        .. rubric:: 行为规约

        - 同步、幂等、无副作用；只是 ``_plugins`` 的只读查询。
        - 非行为：不实例化插件（实例化只发生在 ``use()``）；不触发
          ``install``；不做依赖解析。
        - 边缘情况：``name`` 从未安装 → 默认 ``KeyError`` / ``strict=False``
          下 ``None``；同名插件重复安装的可能性在 ``use()`` 处已被拦截，
          本方法读到的必然是唯一实例。

        :param name: 插件的 ``name`` 类属性值（如 ``"cron"``）。
        :param strict: 未安装时是否抛 ``KeyError``（默认 ``True``；传 ``False``
            则返回 ``None``，用于有意探测）。
        :returns: 插件实例，或未安装且 ``strict=False`` 时的 ``None``。
        :raises KeyError: ``strict=True``（默认）且插件未安装。

        .. rubric:: 测试案例

        - 前置：``use(CronPlugin())`` 后 → 期望：``get_plugin("cron")`` 是该实例；
          ``get_plugin("missing")`` 抛 ``KeyError``；
          ``get_plugin("missing", strict=False) is None``。
        - 前置：未 ``use()`` 任何插件 → 期望：任意 ``name`` 默认抛 ``KeyError``。

        .. rubric:: 调用关系（审计）

        - 调用：无具名符号（读 ``self._plugins``）
        - 被调：``flowing.snapshot``（模块 docstring 与 ``snapshot()`` 插件观测示例）、
          ``flowing._unstable.logging``（install 时探测四扩展，须用 ``strict=False``
          形态）、下游观测代码（``runtime.get_plugin("cron").jobs()`` 式只读 API 查询）
        .. seealso:: :meth:`flowing.runtime.Runtime.use`、
            :class:`flowing.plugins.Plugin`、
            :meth:`flowing.runtime.Runtime.snapshot`
        """
        if strict:
            return self._plugins[name]   # 未安装 → KeyError（直接用写法的快速失败）
        return self._plugins.get(name)   # 未安装 → None（探测写法，须显式 strict=False）

    async def mount(
        self,
        node: str,
        *,
        agent_id: str | None = None,
        **kwargs: Any,
    ) -> ProvideNode:
        """挂载根节点（文件 → 根）；**非阻塞**，返回即节点已就绪。

        .. rubric:: 功能介绍

        创建或恢复 ``parent_id=None`` 的根节点：``node`` 统一为**路径字符串**
        （M-61 最终裁决）——``.fya`` 路径（如 ``"@/root.fya"``）创建 Agent
        根，Workflow 定义文件路径创建 Workflow 根；两种文件形态按解析
        结果区分（Workflow 定义文件内需恰好一个 ``Workflow`` 子类，见
        :mod:`flowing.plugins.workflow`）。**可多次调用**（多根并存，如
        多项目宿主）；不调用也合法（嵌入大程序，按需 ``create_agent()``）。

        **mount / create_agent / recover_agent 对照**：

        - ``mount(path)``：输入是**文件路径**（mount 负责 文件 → 类 的解析
          与装配），挂到对象图上 Runtime 之下成为根（``parent_id=None``），
          其余管线与 ``create_agent`` 完全一致（内部直接委托）。
        - ``create_agent(agent_type)``：输入是**类型名**，新建任意节点。
        - ``recover_agent(id)`` / ``get_agent(id)``：输入是**已有 id**，
          恢复/现场恢复，不新建。

        **幂等挂载（指定 ``agent_id`` 时）**：手动 mount 的根是特殊节点，
        应当指定固定 id——``agent_id`` 已存在于池中 → **走恢复**
        （委托 ``recover_agent``，含休眠记录的现场恢复），而非新建；不存在
        → 新建。第二次重启再 mount 同一文件同一 id，语义是「同一个根
        回来了」，不是「又创建了一个根」。``agent_id=None`` 则每次新建
        新根（自动生成 id）——多根并存/临时根的形态。

        根节点的类型只由 ``node`` 解析决定；``agent_id`` 只是身份指定，
        不改变类型。

        ``**kwargs`` 透传给节点初始化（Agent 形态进 ``setup(**kwargs)``；
        Workflow 形态进其实例化），与 :meth:`create_agent` 的
        ``**kwargs`` 同一契约：写入池元数据持久化、是节点身份的一部分、
        应可 JSON 序列化。

        .. rubric:: 设计动机

        - mount 是「初始化 → 运行」边界；依赖校验**不在**此处（R9 裁决：
          校验在 ``use()`` 时增量执行，与 mount 解绑——mount 只做
          根节点挂载）。
        - 根与子 Agent 走**同一条唯一创建入口**：mount 内部调
          ``create_agent(parent_id=None, **kwargs)``，仅 ``parent_id`` 不同。

        .. rubric:: 使用示例

        .. code-block:: python

            await runtime.mount("@/root.fya")                      # Agent 根（每次新建）
            await runtime.mount("@/root.fya",
                                agent_id="agent-main")             # 固定 id：第二次启动恢复同一根
            await runtime.mount("@/pipelines/release.py",
                                repo="flowing")                    # Workflow 根
            await runtime.mount("@/root.fya", locale="zh")         # 第二个根（不同 id）

        .. rubric:: 行为规约

        - 内部顺序（不变量）：``agent_id`` 非空且在池中 → 委托
          ``recover_agent``；否则 → 创建管线（委托 ``create_agent`` /
          Workflow 等价路径）。mount 不承担依赖校验（R9：校验在
          ``use()`` 时增量执行，与 mount 解绑）。
        - 三语义：**挂接**（解析文件生成子类 / 绑定 ``runtime``；根节点
          ``parent_id=None``，inject 链终点为 ``Runtime._provided``）；
          **实例化**（走完整创建/恢复管线，前置条件是全部 ``use()`` 已完成）；
          **激活**（消息队列进入消费状态，工作循环 Task 后台运行）。
        - 返回后不变量：节点已注册进 ``_nodes``、工作循环已启动、队列为空
          （挂起在 ``await queue.get()``）。**返回 ≠ 有活干**。
        - Workflow 根：无 Turn 循环、不调 LLM；其子 Agent 的 ``parent_id``
          指向 Workflow，provide 链经 Workflow 上溯到 Runtime。
        - ``agent_id`` 仅适用于 ``.fya`` Agent 根；Workflow 根不支持指定
          ``agent_id``（Workflow 的 ``node_id`` 由 Workflow 自身分配），
          传了抛 :class:`ValueError`。
        - 非行为：不接受已构造的实例（实例形态统一走
          ``create_agent`` 等价管线，mount 只做文件 → 节点）；不读取
          消息、不启动网络服务、不等待任何回合结果。

        :param node: ``.fya`` 路径或 Workflow 定义文件路径（支持路径前缀
            规则）。
        :param agent_id: 可选，Agent 根的固定 ``node_id``。已存在于池中 →
            恢复而非新建（幂等挂载）；不存在 → 以该 id 新建；``None`` →
            每次新建（自动生成 id）。类型由 ``node`` 解析决定，本参数不
            指定类型。
        :param kwargs: 节点初始化参数（身份的一部分，见上文）。
        :return: 创建或恢复的根节点（``Agent`` 或 ``Workflow``）。

        :raises FileNotFoundError: 路径不存在时。

        .. rubric:: 测试案例

        - 前置：正常项目 → 操作：``await mount("@/root.fya")`` → 期望：
          返回的 Agent ``node_id in runtime._nodes``，``snapshot()`` 可见
          ``loaded is True``。
        - 前置：已 mount 一个根 → 操作：再次 ``mount(...)`` → 期望：
          创建第二个 ``parent_id=None`` 的根节点，两者并存互不影响。

        .. rubric:: 调用关系（审计）

        - 调用：``self.create_agent(parent_id=None, **kwargs)``（时机：Agent 根路径，见设计动机）；``self.recover_agent``（时机：幂等挂载命中既有 id）；Workflow 等价路径（时机：Workflow 定义文件路径，见 ``flowing.plugins.workflow``）
        - 被调：无框架内调用方（子项目 ``main()`` 新建策略入口；CLI 只经 launch 拿 Runtime）

        .. seealso:: :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.runtime.Runtime.recover_agent`、
            :class:`flowing.plugins.workflow.Workflow`
        """
        resolved = self.resolve_path(node)   # 统一为路径字符串（M-61）
        if not resolved.exists():
            raise FileNotFoundError(f"mount 路径不存在：{resolved}")
        self._ensure_persist_ready()   # 首个 mount 前的持久化就位（persist 目录 + 插件清单 + 兜底引导）
        if resolved.suffix == ".fya":
            # Agent 根：与子 Agent 走同一条唯一创建入口，仅 parent_id=None 不同；
            # 幂等挂载：agent_id 指定且已在池中 -> 恢复而非新建（手动 mount 的
            # 根是特殊节点，固定 id 使第二次启动「同一个根回来了」）；
            # 类型仍由 node 解析决定
            if agent_id is not None and agent_id in self._agent_pool:
                return await self.recover_agent(agent_id, **kwargs)
            return await self.create_agent(node, parent_id=None, agent_id=agent_id, **kwargs)
        if agent_id is not None:
            raise ValueError("agent_id 仅适用于 .fya Agent 根；Workflow 根不支持指定 agent_id")
        # Workflow 根（等价路径）：定义文件内需恰好一个 Workflow 子类
        # 函数体内局部 import 保留并刻意为之（S-43 裁决③）：TYPE_CHECKING 化后这是
        # runtime → plugins.workflow 的唯一运行时边；mount 被调用时两模块均已加载完毕，
        # 局部 import 无循环风险，且使模块头依赖图保持单向。
        from flowing.plugins.workflow import resolve_workflow
        workflow_class = resolve_workflow(node)
        workflow: Workflow = workflow_class()   # 实例化 + 绑定 runtime（等价管线细节见 flowing.plugins.workflow）
        return workflow

    async def create_agent(self, agent_type: str, *, parent_id: str | None = None,
                           agent_id: str | None = None,
                           session_dir: str | Path | None = None,
                           **kwargs: Any) -> Agent:
        """**唯一真正执行创建的代码路径**（纯新建）：分配新 ``agent_id``，走创建管线。

        .. rubric:: 功能介绍

        框架核心层方法。``mount()`` / ``Agent.create_subagent()`` /
        ``Workflow.create_agent()`` 全部委托本方法——委托方只提供「自己的
        ``node_id`` 作为 ``parent_id``」。``agent_type`` 统一为**字符串类型名**
        （非类对象；类对象无法持久化，已被裁决废弃）。

        .. rubric:: 设计动机

        前处理（ID 分配、``runtime`` / ``_parent_id`` 绑定）与后处理
        （``_nodes`` 注册、池元数据写入）都收敛在本方法内——「**不可能创建而不
        注册**」是结构保证。旧 ``Agent.create()`` 类方法直调的四隐患（不在
        ``_nodes``、inject 链断裂、树销毁遗漏、ID 前缀混乱）由唯一入口根除。

        .. rubric:: 使用示例

        .. code-block:: python

            agent = await runtime.create_agent("order-agent", parent_id=None,
                                               order_id="123")

        .. rubric:: 行为规约（管线逐阶段，顺序为不变量）

        1. ``get_agent_class(agent_type)`` —— 类型名 → 类（惰性解析；
           裸名先查注册表，路径形态经 ``resolve_path``）。
        2. ``agent_class.__new__`` → 绑定 ``node_id``（``agent_id`` 指定时即
           该值，须不在池注册表与活体表中，重复抛 ``ValueError``；缺省
           ``f"{_id_prefix}-{uuid4()}"``）、``runtime = self``、
           ``_parent_id = parent_id if parent_id is not None
           else self.node_id``（``None`` 翻译为 Runtime 的 node_id——「根」由
           「父是 Runtime」表达，``_parent_id`` 字段内不出现 ``None``）。
           **目录存在性检查（R22，create 侧严格）**：计算 ``_session_dir``
           后，若该 id 不在池/活体表但**目录已存在** → 抛
           ``FileExistsError``（消息指明「目录已存在：可能是已 archive 的
           留档（``archive_agent``）或指定错了 session_dir / agent_id」；
           runtime 不在此销毁任何内容，由调用方捕获决定——改 id /
           先删目录 / 运维恢复）。例外：``agent_id`` 未指定（框架自动
           生成 uuid）时撞目录不报错，重新生成随机 id（uuid 碰撞概率
           为零，此为防御性兜底）。**id 的权威是 agent 池**（名录），
           目录只是佐证——recover 归档 id 同样报错（池中无即
           ``KeyError``，R9 节归档留档态）。
        3. ``instance.__init__(...)``（同步骨架，**含** ``_open_stores``
           建立持久化后端与 ``_extra``，P3-03 裁决）。
        3b. **身份四键整写 ``meta.json``**（D2/D12）：``agent_type`` /
            ``parent_agent_id`` / ``created_at`` / ``args``，JSON 整写、
            非状态、Runtime 属主。前置条件全部在 setup 前已知（args 来自
            调用方；before_create 对 kwargs 的改写不落盘——恢复时经
            ``before_recover`` 重新表达）。
        4. ``kwargs = await hooks.before_create.dispatch(instance, kwargs)``
           —— 可改写 kwargs。**定位**：``setup()`` 刻意不区分 create /
           recover（两管线各跑一次、作用相同），「只应在创建时做、恢复时
           不做」的逻辑由钩子对承担管线判别——``before_create`` /
           ``after_create`` 仅 create 触发，``before_recover`` /
           ``after_recover`` 仅 recover 触发。注意 handler 只能来自类上
           ``@on`` 声明（实例 hooks 由 ``_init_hooks`` 在 ``__init__``
           注册；插件/Composable 的挂载通道是 setup 里的 ``use_xxx``，
           赶不上本钩子）；与 ``before_tool_call['invoke-subagent']``
           的分工：后者只覆盖工具唤起路径，本钩子覆盖全部创建路径。
           **此刻实例可用的东西（R23）**：骨架已就位——``_extra`` 可写
           （塞运行期对象）、hooks 已建、**可 ``register_state`` 声明
           状态键**（声明只落 defaults 表，不落盘）；state 写透立即可用
           （无写闸门，D5——create 无重放不冲突）。
        5. ``await instance.setup(**kwargs)``。
        6. **PENDING 检查**（固定步骤，非钩子；``PENDING`` 哨兵未兑现则抛错，
           见 ``flowing.parsable.PENDING``）。**S-29 扩展**：同一步检查
           ``instance.hooks._pending_on``——``@on`` 暂记未结算（目标钩子点
           在 setup 结束前未被 declare）→ 抛
           ``flowing.errors.UnknownHookPointError``，消息列出未消费的
           hook_name 与方法名。
        7. 池注册：全局 ``core`` 名录写入新 ``agent_id``（``state("core")
        7. 池注册：全局 ``core`` 名录写入新 ``agent_id``（``state("core")
           ["agents"]`` 追加，写透）+ ``_agent_pool[node_id]``（``agent_type`` /
           ``parent_agent_id`` / ``created_at`` / ``args``）——**args 是
           Agent 身份的一部分**（身份四键已在第 3b 步整写进 ``meta.json``，
           D12；本步仅构建内存池条目，args 用 before_create 改写后的最终值）。
           因此 ``**kwargs`` 应可 JSON 序列化（不可序列化值初版仅约定
           为不被持久化，恢复时缺失）。
        8. ``_nodes[node_id] = instance``（创建即注册，结构保证「不可能创建而
           不注册」）。
        9. ``await hooks.after_create.dispatch(instance)``。
        10. 常驻工作循环 Task 启动，返回 instance。

        - 后置条件：Agent 已激活、就绪、工作循环运行；队列为空时挂起等待。
        - 非行为：不加载持久化状态（那是 ``recover_agent`` 管线的
          ``instance._restore()``）；
          不接受类对象作为 ``agent_type``；不带 ``resume`` 之类的双语义参数。

        :param agent_type: 字符串类型名（裸名或路径形态，经 ``get_agent_class``
            惰性解析）。
        :param parent_id: 父节点 id（``None`` = 根节点，内部翻译为 Runtime 的
            ``node_id``，inject 链直连链终点；API 层的 ``None`` 仅是人体工学
            默认值，不落进实例字段与池元数据）。
        :param session_dir: 该 agent 的 **session 持久化目录**（``tree.jsonl`` /
            ``state.jsonl`` 所在目录）。``None`` → 默认 ``persist_dir / node_id``；
            指定为**绝对路径**原样使用，**相对路径**以 ``runtime._persist_dir``
            为基准解析。持久化存储形式：``persist_dir`` 内存相对形式、根外存
            绝对路径（与 :meth:`to_project_path` 的 M-64 表示约定同构）；
            存入池元数据 ``session_dir`` 字段与 ``core`` 名录 ``session_dirs``
            映射，恢复/池扫描据此定位。属框架机制字段，**不进** args。
        :param agent_id: 指定该 agent 的 ``node_id``（可选）。``None`` → 自动生成
            ``{_id_prefix}-{uuid4()}``；指定值须**不在**池注册表与活体表中
            （与 :meth:`recover_agent` 的「要求已存在」对称：create 要求不存在），
            重复 → :class:`ValueError`。不校验格式，可以使用 ``agent-`` 前缀
            （与自动生成的 ``{_id_prefix}-{uuid4()}`` 形态保持一致，便于看
            id 知类型），建议使用可作目录名的字符（缺省 ``session_dir``
            时即 session 目录名）。
        :param kwargs: 实例化参数，透传 ``before_create`` → ``setup(**kwargs)``，
            并作为 args 持久化。
        :return: 创建并注册完毕的 Agent。
        :raises KeyError: ``agent_type`` 无法解析（注册表与路径均不命中）时。
        :raises ValueError: ``agent_id`` 指定且已存在于池注册表或活体表时。
        :raises flowing.errors.MissingFieldError: PENDING 检查失败（延迟定义
            未兑现）时（S-09 裁决：统一指名具体类；哨兵语义见
            ``flowing.parsable``）。
        :raises flowing.errors.UnknownHookPointError: ``@on`` 暂记未结算
            （``hooks._pending_on`` 非空——目标钩子点在 setup 结束前未被
            declare，S-29）时。

        .. rubric:: 测试案例

        - 前置：``before_create`` handler 改写 kwargs → 期望：``setup()`` 收到改写
          后的 kwargs，池元数据 args 为改写后值。
        - 前置：``setup()`` 留下 ``PENDING`` 未赋值 → 期望：抛错且不写入
          ``_nodes``（管线中断，无半注册实例）。
        - 前置：创建两个 Agent → 期望：``node_id`` 均带 ``agent-`` 前缀且唯一。
        - 前置：``agent_id="agent-xxx"`` 指定且不在池/活体表 → 操作：
          ``create_agent(..., agent_id="agent-xxx")`` → 期望：成功且
          ``node_id == "agent-xxx"``。
        - 前置：``agent_id="agent-xxx"`` 已存在于池（或活体表）→ 操作：同上 →
          期望：抛 :class:`ValueError`，不产生任何注册。

        .. rubric:: 调用关系（审计）

        - 调用：``self.get_agent_class()``（管线第 1 步）；``flowing.hooks.HookList.dispatch``（before_create / after_create，管线第 4、9 步）；``instance.setup()``（管线第 5 步，见 ``flowing.agent.Agent.setup``）；``self.state("core")`` 名录写透（管线第 7 步）
        - 被调：``flowing.runtime.Runtime.mount``（Agent 根路径，每次 mount）；``flowing.agent.Agent.create_subagent``（每次创建子 Agent）；``flowing.plugins.workflow.Workflow.create_agent``（每次 Workflow 创建 Agent）

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.agent.Agent.create_subagent`、
            :meth:`flowing.agent.Agent.setup`
        """
        from uuid import uuid4

        self._ensure_persist_ready()   # 入口就位：persist 目录 + core 插件清单 + 兜底引导
        agent_class = self.get_agent_class(agent_type)   # 第 1 步：类型名 → 类（惰性解析）
        instance: Agent = agent_class.__new__(agent_class)   # 第 2 步：__new__ + 绑定
        if agent_id is not None:
            # 指定 agent_id：须不在池注册表与活体表中（创建冲突即报错——与
            # recover_agent 的「要求已存在」对称；_nodes 含 Workflow 节点，一并防撞）
            if agent_id in self._agent_pool or agent_id in self._nodes:
                raise ValueError(
                    f"agent_id 已存在：{agent_id}——重复创建不允许"
                    "（create 要求不存在，恢复请用 recover_agent）")
            node_id = agent_id
            resolved_session = self._resolve_session_dir(session_dir, node_id)
            # 目录存在性检查（R22，create 侧严格）：id 不在池/活体表但目录
            # 已存在 → FileExistsError（可能是 archive 留档或指定错了
            # session_dir / agent_id；runtime 不销毁任何内容，由调用方决定）
            if resolved_session.exists():
                raise FileExistsError(
                    f"session 目录已存在：{resolved_session}——可能是已 archive "
                    "的留档（archive_agent）或指定错了 session_dir / agent_id；"
                    "请改 id / 先删目录 / 运维恢复")
        else:
            # 缺省自动生成：撞目录不报错，重新生成随机 id（uuid 碰撞概率为零，
            # 此为防御性兜底；session_dir 显式指定时目录即定点，撞了只能报错）
            while True:
                node_id = f"{agent_class._id_prefix}-{uuid4()}"
                resolved_session = self._resolve_session_dir(session_dir, node_id)
                if not resolved_session.exists():
                    break
                if session_dir is not None:
                    raise FileExistsError(
                        f"session 目录已存在：{resolved_session}——指定了已存在的"
                        " session_dir；请检查路径或先删目录")
        instance.node_id = node_id
        instance.runtime = self
        # None 翻译为 Runtime 的 node_id：「根」由「父是 Runtime」表达，
        # _parent_id 字段内不出现 None（inject 链终点可达性的结构保证之一）
        instance._parent_id = parent_id if parent_id is not None else self.node_id
        instance._session_dir = resolved_session   # session 目录（Agent.__init__ 的 _open_stores 使用，骨架期即可知）
        # 管线负责建 session 目录（FileRecordStore 惰性打开句柄时不建父目录；
        # 目录存在性检查已过，此处 mkdir 即「创建即注册」的物理侧）
        resolved_session.mkdir(parents=True, exist_ok=True)
        instance.__init__()   # 第 3 步：同步骨架
        # 第 3b 步（D2/D12）：身份四键整写 meta.json（JSON 整写、非状态、
        # Runtime 属主）。前置条件核对：agent_type/parent_agent_id/args
        # 来自调用方、created_at 现取、session_dir 由管线预绑——全部在
        # setup 前已知，可行；before_create 对 kwargs 的改写不落盘（恢复
        # 时经 before_recover 重新表达，两条路径自洽）
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            json.dumps(kwargs)   # args 应可 JSON 序列化；不可序列化值初版不被持久化（恢复时缺失）
            persisted_args = kwargs
        except TypeError:
            _logger.warning("agent %s 的 args 不可 JSON 序列化，meta.json 按空 args 持久化", node_id)
            persisted_args = {}
        (resolved_session / "meta.json").write_text(json.dumps({
            "agent_type": agent_type,
            "parent_agent_id": instance._parent_id,
            "created_at": created_at,
            "args": dict(persisted_args),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        kwargs = await instance.hooks.before_create.dispatch(instance, kwargs)   # 第 4 步：可改写 kwargs
        await instance.setup(**kwargs)   # 第 5 步
        # 第 6 步：PENDING 检查（固定步骤，非钩子；R-05 落实为模块级
        # _check_pending——含 S-29 扩展的 hooks._pending_on 结算）
        _check_pending(instance, agent_type)
        # 模型初始解析（agent.py 类属性契约「实例化时由 model_tag 解析填充
        # 初始值」的管线落点——spec 管线步骤未具名列出）：setup 已直接赋
        # self.model（ModelConfig 任意模型通道）或改过 model_tag（__setattr__
        # 已重解析）则跳过；解析失败（模型文件缺失 / 标签未定义）即创建失败
        # ——fail fast，不留「创建成功但首次调用才爆雷」的窗口
        if "model" not in instance.__dict__:
            instance.model = instance._resolve_model_tag(instance.model_tag)
        # 第 7 步：池注册——core 名录**追加**（读-改-写写透；spec 骨架的
        # `= [node_id]` 整表替换是笔误，会丢掉既有名录项）
        core = self.state("core")
        core["agents"] = [*core.get("agents", []), node_id]
        core["session_dirs"] = {
            **core.get("session_dirs", {}),
            node_id: self._store_session_dir(instance._session_dir),
        }   # 名录平行映射：agent_id → session 目录（根内相对 / 根外绝对；池扫描与恢复据此定位）
        self._agent_pool[node_id] = {
            "agent_type": agent_type,
            "parent_agent_id": instance._parent_id,   # 存翻译后的实际值，recover 直接回绑
            "created_at": created_at,
            "session_dir": self._store_session_dir(instance._session_dir),   # 自定义 session 目录（None 时为默认路径的存储形式）
            "args": dict(kwargs),     # args 是 Agent 身份的一部分；内存条目用 before_create 改写后的最终值（与 meta.json 的原始值对应，恢复时经 before_recover 再改写）
        }
        self._nodes[node_id] = instance   # 第 8 步：创建即注册
        await instance.hooks.after_create.dispatch(instance)   # 第 9 步
        instance._loop_task = asyncio.create_task(instance._work_loop())   # 第 10 步：常驻工作循环 Task 启动（具名句柄，destroy 第 2 步的取消落点）
        return instance

    async def recover_agent(self, agent_id: str, **override_args: Any) -> Agent:
        """按已有 ``agent_id`` 恢复 Agent：``node_id = agent_id``（身份连续、可重现）。

        .. rubric:: 功能介绍

        框架核心层方法。从 agent 池元数据 + 该 agent 自己的 session 目录
        （``tree.jsonl`` / ``core.jsonl`` / ``state.jsonl`` / ``meta.json``）
        重建实例。与 ``create_agent`` 共用
        管线结构，差异仅两处：``node_id`` 用已有 id；恢复多一步
        ``instance._restore()``（D2 重排：位于 ``before_recover`` 之前）。

        .. rubric:: 设计动机

        - 「新建 vs 恢复」是两条语义不同的路径，塞进一个函数会产生隐式规则
          （反模式），故独立成方法。
        - recover 无独立 ``recover()`` 方法——两条管线各跑一次 ``setup()``，
          故 ``setup()`` 必须可重入（见 ``flowing.agent.Agent.setup``）；恢复专属
          逻辑挂在 ``before_recover`` / ``after_recover`` 钩子上。
        - 恢复路径经 ``setup(**args)`` 触发 ``before_recover`` / ``after_recover``
          钩子对，**不再触发** ``before_create`` / ``after_create``——两条钩子对
          完全独立，不会意外双触发。

        .. rubric:: 使用示例

        .. code-block:: python

            # main 决定新建 or 恢复（子项目策略，框架不特殊处理 --resume）
            async def main(resume: str | None = None) -> Runtime:
                runtime = Runtime()
                if resume is not None:
                    agent = await runtime.recover_agent(resume)        # 续接
                else:
                    await runtime.mount("@/root.fya")                  # 新建
                return runtime

            # 覆盖：恢复同一个 agent 结构，但用新参数
            agent = await runtime.recover_agent("agent-xxx", order_id="789")

        .. rubric:: 行为规约（管线逐阶段，顺序为不变量）

        1. 读池元数据 ``meta = _agent_pool[agent_id]``。
        2. ``get_agent_class(meta["agent_type"])``。
        3. ``args = dict(meta.get("args", {})); args.update(override_args)``
           —— 默认透传持久化 args，override 覆盖。
        4. ``__new__`` → ``node_id = agent_id``（已有 id，**不是**新 UUID）、
           ``runtime``、``_parent_id = meta["parent_agent_id"]``、
           ``_session_dir``（``meta["session_dir"]`` 回绑；缺省
           ``persist_dir / agent_id``，兼容旧数据）。
        5. ``__init__``（同步骨架，含 ``_open_stores`` 建立持久化后端与
           ``_extra``，P3-03 裁决）。
        6. ``await instance._restore()`` —— **只有 recover 有这一步**；
           Agent 双袋重放自己 session 目录的 ``tree.jsonl`` /
           ``core.jsonl`` / ``state.jsonl``（**D2 重排**：前移到
           ``before_recover`` 之前——重放是读/加载，放钩子前无碍）。
        7. ``args = await hooks.before_recover.dispatch(instance, args)``
           （可改写）→ ``await instance.setup(**args)``（触发
           before/after_recover 钩子对，**不再触发** before_create/
           after_create；setup 中写 state 合法且不被重放覆盖——先重放
           后 setup，见规格 §3.3）。
        8. **PENDING 检查**（固定步骤，非钩子；含 S-29 扩展——
           ``hooks._pending_on`` 非空 → ``UnknownHookPointError``，
           同 create 管线第 6 步）。
        9. ``_nodes`` 注册。
        10. ``await hooks.after_recover.dispatch(instance)`` —— 语义从
            「恢复完成」变「setup 完成后的恢复后钩子」。
        11. 常驻工作循环 Task 启动，返回 instance。

        - 恢复后不变量：已持久化消息全部在（可继续对话）；``current_head_id``
          指向消息树中最后持久化的消息；队列待消费消息在，恢复后作为新逻辑 Turn
          处理；进行中的逻辑 Turn 不恢复（未持久化消息丢弃，撕裂末行丢弃）。
        - 恢复**不递归子** agent（子代经「有 key 无 value → 现场恢复」在
          ``get_agent`` 时惰性重建）；但**向上递归父链**（R14：父在池不在
          ``_nodes`` → 逐级 recover 到 Runtime 止；父悬空 → 孤儿警告、
          继续恢复本节点，其 inject 上溯将在断裂处以 MissingProvideError
          告终，运维应跑 ``archive_orphans()`` 清理）。
        - 非行为：不做「半截 Turn 精确续跑」；不触发 ``before_create`` /
          ``after_create``；不校验 ``override_args`` 与创建时 args 的一致性。

        :param agent_id: 池注册表中已有的 agent id。
        :param override_args: 覆盖持久化 args 的参数。
        :return: 恢复并注册完毕的 Agent。
        :raises KeyError: ``agent_id`` 不在池注册表中时（初版仅约定为该内置异常，
            不引入专用异常类型）。

        .. rubric:: 测试案例

        - 前置：agent 创建时 ``order_id="123"`` → 操作：``recover_agent(id)`` →
          期望：``setup`` 收到 ``order_id="123"``。
        - 前置：同上 → 操作：``recover_agent(id, order_id="789")`` → 期望：
          收到 ``order_id="789"``。
        - 前置：创建管线注册过 ``before_create`` handler → 操作：``recover_agent``
          → 期望：``before_create`` **不被**触发，``before_recover`` 被触发。
        - 前置：session 中消息树含完整消息 + 撕裂末行 → 期望：完整消息恢复，
          撕裂末行丢弃，``current_head_id`` 指向最后完整消息。

        .. rubric:: 调用关系（审计）

        - 调用：``self.get_agent_class()``（管线第 2 步）；``flowing.hooks.HookList.dispatch``（before_recover / after_recover）；``instance.setup()``（管线第 7 步）；``instance._restore()``（管线第 6 步，仅 recover，见 ``flowing.agent.Agent._restore``）
        - 被调：``flowing.runtime.Runtime.get_agent``（「有 key 无 value → 现场恢复」，每次触发）；子项目 ``main()`` 恢复策略（用户代码）

        .. seealso:: :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.runtime.Runtime.get_agent`、
            :meth:`flowing.agent.Agent.setup`、
            :meth:`flowing.agent.Agent._restore`
        """
        self._ensure_persist_ready()   # 入口就位：persist 目录 + core 插件清单 + 兜底引导
        meta = self._agent_pool[agent_id]   # 第 1 步：读池元数据；不在池中即 KeyError
        agent_class = self.get_agent_class(meta["agent_type"])   # 第 2 步
        args: dict[str, Any] = dict(meta.get("args", {}))
        args.update(override_args)   # 第 3 步：override 覆盖持久化 args
        instance: Agent = agent_class.__new__(agent_class)   # 第 4 步：node_id 用已有 id（身份连续）
        instance.node_id = agent_id
        instance.runtime = self
        instance._parent_id = meta["parent_agent_id"]   # meta 存的是翻译后实际值（根条目为 Runtime 的 node_id），直接回绑
        # 第 4b 步（R14）：父链可达性——父在池但不在 _nodes → 逐级向上
        # recover_agent(parent_id)（到 Runtime 止：根条目的父是 runtime-0，
        # 在 _nodes 中即终止；inject 上溯依赖父链完整，父缺位会在子恢复后
        # 造成 MissingProvideError 假故障）；父悬空（既不在 _nodes 也不在
        # 池、且非 runtime-0）→ warnings.warn 孤儿警告，仍继续恢复本节点
        # （不抛错，保持可用性；运维应跑 archive_orphans() 清理）
        recover_parent = instance._parent_id
        if recover_parent != self.node_id and recover_parent not in self._nodes:
            if recover_parent in self._agent_pool:
                await self.recover_agent(recover_parent)
            else:
                warnings.warn(
                    f"recover_agent：{agent_id} 的父节点 {recover_parent} 悬空"
                    "（不在 _nodes 也不在池）——按孤儿继续恢复本节点，inject "
                    "上溯将在断裂处以 MissingProvideError 告终；请经 "
                    "archive_orphans() 清理")
        instance._session_dir = self._load_session_dir(meta.get("session_dir"), agent_id)   # 自定义 session 目录回绑（缺省 persist_dir/agent_id，兼容旧数据）
        if not instance._session_dir.exists():
            # 名录在案但目录缺失：可诊断告警 + 按空 session 容忍（与
            # Agent._restore 的「按空 session 处理并报出可诊断错误」同裁）；
            # mkdir 使 _restore 的压缩 sync 有落点（FileRecordStore 不建父目录）
            warnings.warn(
                f"recover_agent：{agent_id} 的 session 目录缺失"
                f"（{instance._session_dir}）——按空 session 恢复")
            instance._session_dir.mkdir(parents=True, exist_ok=True)
        instance.__init__()   # 第 5 步：同步骨架
        # 第 6 步（D2 重排）：_restore 前移到 setup 前——双袋重放（core +
        # default）先做完，setup 中写 state 不再被重放覆盖、读 state 可见
        # 持久值（闸门职责由「先重放后 setup」结构性替代，见规格 §3.3）
        await instance._restore()   # 仅 recover 有；重放 tree.jsonl / core.jsonl / state.jsonl（后端已在 __init__ 建立，P3-03）
        args = await instance.hooks.before_recover.dispatch(instance, args)   # 可改写 args
        await instance.setup(**args)   # 触发 before/after_recover 钩子对（不触发 before/after_create）
        # 第 7 步：PENDING 检查（同 create 管线——R-05 落实为模块级
        # _check_pending，含 S-29 的 _pending_on 结算检查）
        _check_pending(instance, meta["agent_type"])
        # 模型初始解析（同 create 管线落点；setup 已直接赋 self.model 则跳过）
        if "model" not in instance.__dict__:
            instance.model = instance._resolve_model_tag(instance.model_tag)
        self._nodes[agent_id] = instance   # 第 8 步：创建即注册
        await instance.hooks.after_recover.dispatch(instance)   # 第 9 步
        instance._loop_task = asyncio.create_task(instance._work_loop())   # 第 10 步：常驻工作循环 Task 启动（具名句柄，destroy 第 2 步的取消落点）
        return instance

    def get_node(self, node_id: str, *, strict: bool = True) -> ProvideNode | None:
        """按 ID 从 ``_nodes`` 取节点（共享 ID 空间单点查表）。

        .. rubric:: 功能介绍

        框架核心层方法，``inject_from`` 上溯与销毁子树定位的查表入口。
        因 ID 前缀区分类型（``runtime-`` / ``workflow-`` / ``agent-``），看 ID
        即知类型，单 dict 即可。

        .. rubric:: 设计动机

        被排除的替代方案：纯 UUID 无前缀（不知类型）、分类型注册表（get_node
        O(n)）、``TypedNodeId`` 值对象（过重）。

        .. rubric:: 使用示例

        .. code-block:: python

            parent = runtime.get_node(agent._parent_id)

        .. rubric:: 行为规约

        - 只查活实例注册表（``_nodes``）；已 ``destroy()`` 的节点不在其中
          （其池记录/session 仍保留，属 ``get_agent`` 的现场恢复语义）。
        - 非行为：不触发任何恢复逻辑。
        - ``strict``（C-01 同构，对齐 :meth:`get_plugin`）：``True``（默认）
          未命中抛 ``KeyError``——直接用写法（``inject_from`` 上溯依赖
          此形态）；``False`` 未命中返回 ``None``——探测写法。

        :param node_id: 节点 ID（带前缀）。
        :param strict: 未命中时是否抛 ``KeyError``（默认 ``True``；传
            ``False`` 返回 ``None``，用于有意探测）。
        :return: 节点实例；``strict=False`` 且未注册时为 ``None``。
        :raises KeyError: ``strict=True``（默认）且 ``node_id`` 未注册时。

        .. rubric:: 测试案例

        - 前置：``create_agent`` 返回的 agent → 期望：
          ``runtime.get_node(agent.node_id) is agent``。

        .. rubric:: 调用关系（审计）

        - 调用：无（``_nodes`` dict 查表）
        - 被调：``flowing.runtime.inject_from``（沿 ``_parent_id`` 上溯逐级查表，每次 inject）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent`、
            :func:`flowing.runtime.inject_from`
        """
        if strict:
            return self._nodes[node_id]   # 未注册即 KeyError（直接用写法的快速失败）
        return self._nodes.get(node_id)   # strict=False：探测写法，未注册 -> None

    async def get_agent(self, agent_id: str, *, strict: bool = False) -> Agent | None:
        """按 ``agent_id`` 查 agent 池；**「有 key 无 value → 现场恢复」**统一语义的入口。

        .. rubric:: 功能介绍

        框架核心层方法。池中有活实例直接返回；池记录存在但实例不存在（主 agent
        恢复后未实例化的子 agent、或已 ``destroy()`` 但 session 保留的匿名子
        agent）时，内部走 ``recover_agent(agent_id)`` 现场重建后返回。

        .. rubric:: 设计动机

        统一语义覆盖两场景，调用方无需区分「从未实例化」与「已销毁」；恢复代价
        与树大小无关（主 agent 恢复不递归子 agent），恢复后上下文延续——
        「同一个子 agent」有记忆。

        .. rubric:: 使用示例

        .. code-block:: python

            agent = await runtime.get_agent(child_id)
            result = await agent.query(...)         # 恢复后继续调用

        .. rubric:: 行为规约

        - 现场恢复流程：查池 → 见 key 无 value → 读子 agent 的 ``meta.json``
          （身份）+ ``tree.jsonl`` / ``core.jsonl`` / ``state.jsonl``
          （按元数据 ``agent_type`` 重建实例）→ 重建消息级树/状态
          → 绑定 session → 返回实例。
        - 因可能触发异步恢复管线，本方法是协程。
        - 非行为：不递归恢复子 agent 的子 agent（逐层惰性）；不做模糊匹配。
        - ``strict``（C-01 同构，对齐 :meth:`get_plugin`）：``False``（默认）
          完全不在池中返回 ``None``——探测写法（三个场景的
          ``if agent is None: create`` 分支依赖此默认）；``True`` 完全不在
          池中抛 ``KeyError``——直接用写法。注意 strict 只作用于「完全不
          在池」；在池的活体/现场恢复路径不受其影响。

        :param agent_id: 池注册表中的 agent id。
        :param strict: 完全不在池中时的行为（默认 ``False`` 返回 ``None``；
            传 ``True`` 抛 ``KeyError``）。
        :return: 活 Agent 实例（原有的或现场恢复的）；``strict=False`` 且
            完全不在池中时为 ``None``。
        :raises KeyError: ``strict=True`` 且 ``agent_id`` 完全不在池中时。

        .. rubric:: 测试案例

        - 前置：子 agent 已 ``destroy()``、session 目录保留 → 操作：
          ``await get_agent(child_id)`` → 期望：返回恢复实例，历史消息可见
          （上下文延续），``node_id == child_id``。
        - 前置：主 agent ``recover_agent`` 之后 → 操作：``await get_agent(child_id)``
          → 期望：此刻子 agent 才实例化（此前 ``snapshot()`` 中未加载）。
        - 前置：``agent_id`` 完全不在池中 → 操作：``await get_agent(x)`` → 期望：
          返回 ``None``（不抛错、不模糊匹配）。

        .. rubric:: 调用关系（审计）

        - 调用：``self.recover_agent(agent_id)``（时机：「有 key 无 value → 现场恢复」，见行为规约）
        - 被调：无框架内调用点（docstring 与 ``flowing.interfaces.run.cmd_test`` 示例为调用方代码）

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.agent.Agent.destroy`
        """
        if agent_id in self._nodes:
            return self._nodes[agent_id]   # 活实例直接返回
        if agent_id in self._agent_pool:
            # 「有 key 无 value → 现场恢复」：读 tree.jsonl + state.jsonl 按元数据重建（在 recover_agent 内）
            return await self.recover_agent(agent_id)
        if strict:
            raise KeyError(agent_id)   # strict=True：直接用写法的快速失败
        return None   # 默认探测形态：完全不在池中返回 None（不模糊匹配）

    async def archive_agent(self, node_id: str) -> list[str]:
        """从运行时**完全归档**一个节点及其整棵子树（递归清除，保留文件）。

        .. rubric:: 功能介绍

        框架核心层方法。把 ``node_id`` 及其全部后代从运行时清除：
        ``_nodes``（活体表，经 ``destroy()`` 摘除）、``_agent_pool``
        （池注册表，仅 Agent 有条目）、全局 ``core`` 名录（``state("core")
        ["agents"]``，写透）。**保留文件**：各 session 目录（``tree.jsonl`` /
        ``core.jsonl`` / ``state.jsonl`` / ``meta.json``）原样留档——归档 ≠
        删除记录。``node_id`` 可为任意
        ``_nodes`` / 池成员（Agent 或 Workflow——二者都注册 ``_nodes``、都有
        ``_parent_id`` 与 ``destroy()``，本方法不区分类型）。

        .. rubric:: 设计动机

        - **destroy ≠ archive**：``destroy()`` 只丢实例、保留池 key 与名录
          （「有 key 无 value → 现场恢复」）；``archive_agent`` 把 key 与名录
          一并移除——归档后 ``get_agent`` 返回 ``None`` / ``recover_agent``
          找不到，运行时完全遗忘，文件留档供审计 / 手动恢复（外部运维）。
        - **收集走通用 parent 链**（双来源）：池 ``parent_agent_id``（覆盖
          全部 Agent 子代，含已 destroy 的池条目）+ ``_nodes`` ``_parent_id``
          （覆盖在 ``_nodes`` 但不在池的节点）。``Agent._children`` 只装活
          实例，不可作为收集依据。
        - **销毁多态**：对仍存活的节点 ``await destroy()``——Agent /
          Workflow 各自实现（Workflow 的 destroy 级联其子 Agent），本方法
          不区分类型。

        .. rubric:: 行为规约

        - 流程：双来源收集子树 → 清理各被归档节点**父侧**引用（父 Agent 的
          ``child_ids`` 条目移除并写透）→ 对仍存活的节点 ``await destroy()``
          → 从 ``_agent_pool`` 与 ``core`` 名录移除（写透；不在池的节点
          pop 幂等）。
        - 返回：被归档的 ``node_id`` 列表（含自身与全部后代）。
        - 错误：``node_id`` 在 ``_nodes`` 与 ``_agent_pool`` 中均不命中 →
          ``KeyError``（不做模糊匹配）。
        - 归档后不变量：节点不在 ``_nodes`` 与 ``_agent_pool``；``core``
          名录不含这些 id；session 目录与文件保留。
        - 非行为：**不删除任何 session 目录 / 文件**；不递归恢复；不影响
          ``shutdown()``（归档节点不在 ``_nodes``，销毁循环自然跳过）。
        - 边缘情况：归档根节点（``parent_id == "runtime-0"``）合法；归档
          后孤儿 session 目录保留（名录无 id + 目录存在 = **归档留档态**，
          不报错、不可自动恢复）。

        .. rubric:: 测试案例

        - 前置：A 有子 B、B 有子 C（均存活）。操作：``await
          runtime.archive_agent(a_id)`` → 期望：返回 ``[a_id, b_id, c_id]``；
          ``_nodes`` / ``_agent_pool`` / ``core`` 名录均不含三者；
          session 目录均在；``get_agent(b_id)`` 返回 ``None``。
        - 前置：子 B 已 ``destroy()``（池条目保留、无活实例）。操作：
          ``archive_agent(a_id)`` → 期望：B 的池条目与名录同样被移除。
        - 前置：``node_id`` 在 ``_nodes`` 与池中均不命中 → 操作：
          ``archive_agent(x)`` → 期望：``KeyError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.destroy()`` / 其它 ``_nodes`` 成员的
          ``destroy()``（时机：对仍存活的子树节点，多态分派）；
          ``self.state("core")`` 名录写透（时机：归档集合确定后）；父实例
          ``_state_bag["child_ids"]`` 写透（时机：父存活时）
        - 被调：``Workflow.destroy()``（时机：级联归档其子 Agent）；
          ``archive_orphans()``（时机：每个孤儿）；应用层运维 / 管理 UI 的
          归档入口

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent`、
            :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.runtime.Runtime.archive_orphans`
        """
        if node_id not in self._nodes and node_id not in self._agent_pool:
            raise KeyError(node_id)   # 双来源均不命中即抛错（不做模糊匹配）
        # 1. 双来源递归收集子树：① 池链（parent_agent_id，覆盖全部 Agent 子代
        #    含已 destroy 的池条目）；② _nodes 链（_parent_id，覆盖在 _nodes
        #    但不在池的节点）。两来源重叠去重。
        to_archive: list[str] = []
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in to_archive:
                continue
            to_archive.append(cur)
            for pid, meta in self._agent_pool.items():
                if pid not in to_archive and meta.get("parent_agent_id") == cur:
                    stack.append(pid)
            for nid, node in self._nodes.items():
                if nid not in to_archive and getattr(node, "_parent_id", None) == cur:
                    stack.append(nid)
        # 2. 清理父侧 child_ids（父为活 Agent 时，写透整表）——archive 是显式
        #    遗忘通道，对「child_ids 只增不改」的受控例外；child_ids 核心键
        #    在 core 袋（D1，property 透传），读经 parent.child_ids、写经
        #    _core_state 整表写
        for aid in to_archive:
            meta = self._agent_pool.get(aid)
            parent_id: str | None = (
                meta.get("parent_agent_id") if meta is not None
                else getattr(self._nodes.get(aid), "_parent_id", None))
            parent = self._nodes.get(parent_id) if parent_id else None
            if parent is not None and parent is not self and hasattr(parent, "child_ids"):
                # Workflow 等非 Agent 节点无 child_ids property——跳过（A15 配套）
                ids = dict(parent.child_ids)
                for name, cid in list(ids.items()):
                    if cid == aid:
                        del ids[name]
                parent._core_state["child_ids"] = ids
        # 3. destroy 仍存活的节点（多态分派：Agent.destroy / Workflow.destroy——
        #    后者级联归档其子；已归档子跳过，互调幂等）
        for aid in to_archive:
            node = self._nodes.get(aid)
            if node is not None:
                await node.destroy()
        # 4. 池注册表 + core 名录移除（写透持久化；不在池的节点 pop 幂等）
        for aid in to_archive:
            self._agent_pool.pop(aid, None)
        archived = set(to_archive)
        core_agents = list(self.state("core").get("agents", []))
        self.state("core")["agents"] = [a for a in core_agents if a not in archived]
        return to_archive

    async def archive_orphans(self) -> list[str]:
        """归档全部 **parent 悬空**的池条目（孤儿），逐个递归连同各自子树。

        .. rubric:: 功能介绍

        框架核心层方法。枚举 ``_agent_pool`` 中 ``parent_agent_id`` **悬空**
        的 Agent 条目（父 id 既不在 ``_nodes`` 也不在 ``_agent_pool``），
        对每个经 :meth:`archive_agent` 递归归档（孤儿自身可能还有子树，
        一并清除）。返回全部被归档的 ``node_id`` 列表。

        .. rubric:: 设计动机

        父节点可能不持久化（如 Workflow 运行状态不落盘、崩溃后节点消失），
        而子 Agent 走标准创建管线持久化入池——崩溃重启后子条目
        ``parent_agent_id`` 悬空（父既不在 ``_nodes`` 也不在池）。正常关闭
        路径由父节点自身的 ``destroy()`` 级联处理；崩溃路径无法执行级联，
        由本方法在恢复后运维清理。

        .. rubric:: 行为规约

        - **悬空判定**：``parent_agent_id`` 非空，且不在 ``self._nodes``
          也不在 ``self._agent_pool``。根节点（父为 ``runtime-0``，在
          ``_nodes`` 中）、活 Workflow 子代（父在 ``_nodes``）、父在池的
          条目均**不**判定为孤儿。
        - 每个孤儿经 :meth:`archive_agent` 递归归档（含其子树）；孤儿之间
          无父子重叠（子条目因父在池而不入选），归档安全。
        - 无孤儿 → 返回空列表（幂等，可随时调用）。
        - 非行为：不校验「parent 悬空」是否确由崩溃造成（可能是父被归档后
          的残留——归档父本就递归含子，正常路径不产生，但本方法不区分来源，
          一律清理）。
        - 与 recover 的关系（R14）：``recover_agent`` 遇孤儿父只
          ``warnings.warn`` 警告、继续恢复本节点，不自动归档——归档是
          显式运维动作，由本方法承载。

        .. rubric:: 测试案例

        - 前置：Workflow W 创建子 B 后进程崩溃；重启后池含 B（
          ``parent_agent_id == w_id``，W 不在 ``_nodes`` 不在池）。操作：
          ``await runtime.archive_orphans()`` → 期望：返回 ``[b_id]``，
          B 从池/名录移除、session 保留。
        - 前置：无孤儿（全部条目 parent 在 ``_nodes`` 或池中）→ 操作：
          ``archive_orphans()`` → 期望：返回 ``[]``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.archive_agent``（时机：每个孤儿，
          递归归档含子树）
        - 被调：无框架内调用点（崩溃恢复后的运维清理入口）

        .. seealso:: :meth:`flowing.runtime.Runtime.archive_agent`、
            :meth:`flowing.plugins.workflow.Workflow.destroy`
        """
        orphans = [
            aid for aid, meta in self._agent_pool.items()
            if meta.get("parent_agent_id")
            and meta["parent_agent_id"] not in self._nodes
            and meta["parent_agent_id"] not in self._agent_pool
        ]
        archived: list[str] = []
        for aid in orphans:
            archived.extend(await self.archive_agent(aid))   # 递归归档含各自子树
        return archived

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """根级 provide 注册（inject 链终点的供方）。

        .. rubric:: 功能介绍

        ``ProvideNode`` 协议实现。注册的值对全树所有节点可见（上溯终点）。
        ``InjectionKey[T]`` 仅作编译期类型标注；``_provided`` 的 key 始终是
        ``str``，类型信息不跨节点传递。

        .. rubric:: 设计动机

        应用层概念（宿主工程根、locale、trace adapter）经 provide 注入而非进
        ``@/`` 路径体系；同 key 重复 provide 即覆盖更新（运行时变更的正规
        通道，如运行时切换 locale——spec-draft §13.4）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.provide("locale", "zh")
            runtime.provide("workspace_root", workspace_root)

        .. rubric:: 行为规约

        - 时序（约定）：影响 Agent **装配期**（setup / 装配钩子）读取的
          provide 值应在 ``mount()`` / ``create_agent()`` 之前注册——装配期
          inject 未命中即 ``MissingProvideError``；运行期 provide / 覆盖
          合法（inject 实时查找即刻可见），是运行时变更的正规通道。
        - 同 key 重复 provide = **覆盖更新**（后者生效——spec-draft
          §13.4「provide 值可运行时更新」；``inject`` 每次实时沿链
          查找、不缓存，更新即刻对后续 inject 可见）。防插件间静默
          覆盖由 ``InjectionKey`` 前缀约定承担，机制不拦截（S-37
          裁决：pyi 草稿的「后注册者报错」与 §13.4 冲突，废弃）。
        - 非行为：value 不做序列化、不进持久化；**API key / 凭证等敏感信息禁止
          经 provide 传递**（不进消息、不落盘的安全边界）。

        :param key: provide key（字符串，或 ``InjectionKey`` 标注）。
        :param value: 任意对象。

        .. rubric:: 测试案例

        - 前置：``runtime.provide("k", 1)`` → 操作：子 Agent ``inject("k")`` →
          期望：命中 ``1``（链终点兜底）。
        - 前置：``provide("k", 1)`` 后 ``provide("k", 2)`` → 期望：
          后续 ``inject("k")`` 命中 ``2``（覆盖更新即刻可见）。

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_provided``）
        - 被调：各插件 ``install()``（``runtime.provide(...)``，时机：阶段一安装；示例见 ``flowing.plugins`` / ``flowing.plugins.comm`` / ``flowing.plugins.cron`` / ``flowing.plugins.skills`` / ``flowing._unstable.logging``）

        .. seealso:: :class:`flowing.runtime.ProvideNode`、
            :func:`flowing.runtime.inject_from`、
            :class:`flowing.params.InjectionKey`
        """
        k = str(key)   # InjectionKey[T] 仅编译期类型标注；_provided 的 key 始终是 str
        # 同 key 重复 provide = 覆盖更新（spec-draft §13.4；inject 实时查找即刻可见）
        self._provided[k] = value   # 敏感信息（API key / 凭证）禁止进入

    def inject(self, key: str | InjectionKey[T]) -> T:
        """根级 inject：在 ``Runtime._provided`` 查找（链终点），未命中抛错。

        .. rubric:: 功能介绍

        ``ProvideNode`` 协议实现，等价于 ``inject_from(self, self, key)``——
        Runtime 无 ``_parent_id``，查找即终点。

        .. rubric:: 使用示例

        .. code-block:: python

            locale = runtime.inject("locale")

        .. rubric:: 行为规约

        - 只查根级 ``_provided``，无上溯（已是终点）。
        - 非行为：不查 ``_config_overrides`` / ``_resources``（三个存储
          语义独立：provide 跟随节点生命周期，Resource 跨 Agent 树外直引，
          config 覆盖层是运行期配置复写）。

        :param key: provide key。
        :return: 命中值。
        :raises flowing.errors.MissingProvideError: 未命中时，携带 ``key``。

        .. rubric:: 测试案例

        - 前置：未 provide ``"nope"`` → 操作：``runtime.inject("nope")`` →
          期望：``MissingProvideError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.inject_from(self, self, key)``（时机：每次调用，见功能介绍）
        - 被调：无框架内调用点（Agent / Workflow 的 inject 走各自实现）

        .. seealso:: :func:`flowing.runtime.inject_from`、
            :exc:`flowing.errors.MissingProvideError`
        """
        result: T = inject_from(self, self, key)   # 链终点查找（Runtime 无 _parent_id，等价语义见功能介绍）
        return result

    def get_config(self, key: str | ConfigKey[T], default: T | None = None) -> T | None:
        """配置读取（实例方法，读取对称原则）；优先级链浅合并后的最终值。

        .. rubric:: 功能介绍

        框架核心层方法。读取按优先级链（环境变量 > 命令行 > 用户级 > 项目级 >
        默认值）**浅合并**后的配置值。``ConfigKey[T]`` 约束 ``default`` 的类型
        与泛型参数一致（mypy/pyright 可检查）。

        .. rubric:: 设计动机

        - 读取全部是实例方法，不做模块级全局函数——不存在「隐式拿到某个
          Runtime」的旁路。
        - 无命名空间访问控制：注册只是「谁负责校验」的声明；未注册命名空间
          静默保留、可自由读取。

        .. rubric:: 使用示例

        .. code-block:: python

            timeout = self.get_config("agent.timeout", default=60)   # Agent.setup 内
            lang = runtime.get_config("i18n.default_lang")           # 未注册命名空间也可读

        .. rubric:: 行为规约

        - **调用时机约束（黑名单式）**：约束实质是**就绪时点**——优先级链
          合并完成（launch 引导）之前，典型即模块顶层 import 期，调用抛
          ``ConfigNotReadyError``；合并完成后**任何时机**可调用（setup()、
          钩子回调、工具 callable、``main()`` 后续代码、插件运行期方法等），
          读取为现场求值。
        - 浅合并语义：同名 key 高优先级覆盖；未覆盖的 key 沿用低优先级值；
          列表值整列表覆盖（不合并）。
        - 读取顺序：``_config_overrides``（``set_config`` 运行期覆盖层）
          命中优先，其次为优先级链浅合并结果。
        - 非行为：不校验 key 属于哪个命名空间；不写回配置文件（写入走
          ``set_config``，不持久化）。

        :param key: 配置 key（点分字符串或 ``ConfigKey[T]``）。
        :param default: key 不存在时的默认值；类型须与 ``ConfigKey[T]`` 一致。
        :return: 配置值或 ``default``。
        :raises flowing.errors.ConfigNotReadyError: 配置未就绪（模块顶层调用）时。

        .. rubric:: 测试案例

        - 前置：项目级 ``agent.timeout: 60``、用户级 ``agent.timeout: 120`` →
          期望：``get_config("agent.timeout") == 120``；
          ``get_config("agent.max_turns") == 20``（未覆盖沿用低优先级）。
        - 前置：模块顶层调用 → 期望：``ConfigNotReadyError``。

        .. rubric:: 调用关系（审计）

        - 调用：无（读优先级链浅合并后的配置值）
        - 被调：无框架内调用点（消费侧为扩展的 ``setup()`` / 钩子回调，示例见 ``flowing.params.ConfigKey``）

        .. seealso:: :meth:`flowing.runtime.Runtime.register_config_namespace`、
            :meth:`flowing.runtime.Runtime.set_config`、
            :class:`flowing.params.ConfigKey`
        """
        # 调用时机约束（黑名单式）：优先级链合并完成（__init__ 尾部）之前——
        # 典型即模块顶层 import 期——调用抛 ConfigNotReadyError；就绪判定 =
        # _config_ready 闸（R-06 落实，__dict__ 读取兼管子类 super().__init__()
        # 之前的过早调用）
        if not self.__dict__.get("_config_ready", False):
            raise ConfigNotReadyError()   # 无字段叶子（固定英文提示消息，X14）
        k = str(key)
        if k in self._config_overrides:
            return self._config_overrides[k]   # set_config 运行期覆盖层优先
        return self._merged_config.get(k, default)   # 浅合并产物（点分扁平 key）；未注册命名空间静默保留、可自由读取；ConfigKey[T] 约束 default 类型

    def set_config(self, key: str, value: Any) -> None:
        """运行期配置覆盖（``_config_overrides`` 层，``get_config`` 读取时最优先）。

        功能与动机：下游产品运行期复写框架配置的通道（``main()`` 内读
        产品自己的配置文件 → 命中则 ``runtime.set_config("agent.timeout",
        ...)``）；取代旧的 ``_settings`` 独立存储（``set_setting`` /
        ``get_setting`` 已删除——独立口袋与配置链互不兑现是双源误导，
        R17 裁决：完全靠 get/set config）。

        行为边界：同 key 覆盖写（后写胜出）；**不持久化**（进程级，
        重启即失效——持久覆盖请改配置文件/环境变量）；即时生效语义以各
        读取方为准（``get_config`` 现场求值），框架不广播变更（无响应式
        系统）。

        :param key: 配置 key（点分字符串，可覆盖 key 即 §6 已知清单：
            ``agent.timeout`` / ``agent.max_turns`` / ``agent.max_depth`` /
            ``runtime.log_level``，及扩展注册的命名空间 key）。
        :param value: 覆盖值。

        .. rubric:: 使用示例

        .. code-block:: python

            product_cfg = load_yaml("config.local.yaml")   # 产品自己的文件
            if "agent.timeout" in product_cfg:
                runtime.set_config("agent.timeout", product_cfg["agent.timeout"])

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_config_overrides``）
        - 被调：无（框架内零引用；下游产品复写模式入口，见功能介绍）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_config`
        """
        self._config_overrides[key] = value   # 同 key 覆盖写（后写胜出；不持久化）

    @property
    def config(self) -> dict[str, Any]:
        """项目配置合并视图（Parsable 渲染上下文的 ``config`` 入口）。

        功能与动机：优先级链浅合并产物（``_merged_config``）的**嵌套**形态
        ——模板里 ``{{ config.agent.timeout }}`` 逐级取值（Jinja attr→item
        回退）；``get_config`` 消费的是点分扁平形态。现场反摊平，只读语义
        ——改写返回的 dict 不回写框架。不含 ``set_config`` 的运行期覆盖层
        （渲染上下文契约是「配置合并视图」，覆盖层只服务 ``get_config``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（读 ``_merged_config`` 反摊平）
        - 被调：``flowing.parsable``（渲染上下文组装，每次模板求值）
        """
        nested: dict[str, Any] = {}
        for dotted, value in self.__dict__.get("_merged_config", {}).items():
            parts = dotted.split(".")
            cursor = nested
            for part in parts[:-1]:
                nxt = cursor.setdefault(part, {})
                if not isinstance(nxt, dict):
                    break   # 叶子与命名空间撞名：保留先到者（配置组织异味，不报错）
                cursor = nxt
            else:
                cursor[parts[-1]] = value
        return nested

    @overload
    def get_resource(self, name: str) -> Any: ...
    @overload
    def get_resource(self, name: str, type_hint: type[T]) -> T: ...
    def get_resource(self, name: str, type_hint: type[T] | None = None) -> Any:
        """共享 Resource 读取；``type_hint`` 仅 IDE 推断，运行时不做 isinstance 校验。

        .. rubric:: 功能介绍

        框架核心层方法。Resource 是跨任务、跨 Agent 树共享的实例存储（知识库、
        连接池等），生命周期由 Runtime 管理、独立于任何 Agent——因此**不走**
        inject 链（provide/inject 的值跟随 Agent 生命周期，Resource 在树外直引）。

        .. rubric:: 设计动机

        读写通道对称：``register_resource`` / ``get_resource`` 都是实例方法；
        旧的模块级全局 ``get_resource()``（TLS/contextvar 隐式拿 Runtime）已废弃。
        Agent 便捷方法 ``Agent.get_resource()`` 委托 ``self.runtime``；工具
        callable 经 ``caller.get_resource(...)`` 统一入口。

        .. rubric:: 使用示例

        .. code-block:: python

            # main.py
            runtime.register_resource("kb", KnowledgeBase("./index/docs"))

            # 工具 callable 内（框架自动注入 caller）
            async def search_kb(query: str, caller: Agent = None) -> list[str]:
                kb = caller.get_resource("kb", KnowledgeBase)
                return await kb.search(query)

        .. rubric:: 行为规约

        - name 未注册 → ``ResourceNotFoundError``。
        - ``type_hint`` 不做运行时校验（纯类型标注通道）。
        - 非行为：不惰性创建（Resource 在 ``mount()`` 前由 ``register_resource``
          显式注册完毕）；不经 provide 链查找。

        :param name: Resource 注册名。
        :param type_hint: 期望类型（仅类型检查器与 IDE 使用）。
        :return: 注册实例。
        :raises flowing.errors.ResourceNotFoundError: name 未注册时。

        .. rubric:: 测试案例

        - 前置：``register_resource("db", pool)`` → 期望：
          ``runtime.get_resource("db") is pool``。
        - 前置：未注册 → 期望：``ResourceNotFoundError``。

        .. rubric:: 调用关系（审计）

        - 调用：无（``_resources`` 查表）
        - 被调：``flowing.agent.Agent.get_resource``（便捷委托，经 ``self.runtime``）；工具 callable 经 ``caller.get_resource(...)``（时机：每次工具执行，见 ``flowing.tool``）

        .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`、
            :meth:`flowing.agent.Agent.get_resource`
        """
        if name not in self._resources:
            raise ResourceNotFoundError(name)
        return self._resources[name]   # type_hint 仅 IDE 推断，运行时不做 isinstance 校验

    def register_tool(self, tool: Tool | str, *, namespace: str | None = None) -> None:
        """注册全局工具（``ns::规范名`` → ``Tool``，写入 ``tool_registry``）。

        功能与动机：插件三类资源注册之一（R1：install 只注册）；全局注册表存
        「可执行对象 + 默认 LLM 可见声明」，Agent 级差异由 ``ToolEntry`` 绑定层
        覆写，不改全局注册表（能力三正交：对象 / 声明 / 绑定）。

        **两种入参形态**（R18 裁决）：

        - 已构造的 ``Tool`` 实例——直接注册；
        - **文件路径字符串**（``.fya`` / ``.py``，支持 ``@/`` ``./`` 等前缀
          规则）——先经 ``ToolRegistry.get`` 的文件解析链构造出 Tool 再注册
          （插件随身携带的文件形态工具即在 ``install()`` 中经此通道解析
          注册；基准目录显式给出，不经 Agent 的 ``source_dir`` 链）。

        行为边界：``ns::name`` 全键冲突时后注册者报错；注册只在 ``install``
        （R3：运行时不增删）；Agent 侧按**别名**查 ``_tool_entries``（见
        ``flowing.tool``）。命名空间语义见模块 docstring §7a：``namespace``
        **显式指定时与文件路径无关**（覆盖目录派生）；缺省时——实例形态落入
        ``default::``（裸名视图优先层——往 ``default::`` 注册与核心同名的
        工具即**覆盖原生行为**，被覆盖者仍可用 ``builtin::name`` 显式引用），
        文件形态从所在目录派生命名空间（§7a）；自定义命名空间的资源只能以
        ``ns::name`` 全限定名引用。

        :param tool: 已构造的 ``Tool`` 实例，或工具定义文件路径字符串
            （``.fya`` / ``.py``）。
        :param namespace: 命名空间；``None`` → 实例形态 ``"default"`` /
            文件形态按目录派生；显式指定时与文件路径无关。

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``tool_registry``）
        - 被调：各插件 ``install()``（``runtime.register_tool(...)``，时机：阶段一注册；示例见 ``flowing.plugins`` / ``flowing.plugins.cron`` / ``flowing.plugins.skills`` / ``flowing.plugins.workflow``）；``Runtime.__init__``（内置工具以 ``namespace="builtin"`` 注册）

        .. seealso:: :class:`flowing.tool.Tool`、:class:`flowing.tool.ToolEntry`、
            :attr:`flowing.runtime.Runtime.tool_registry`
        """
        if isinstance(tool, str):
            # 文件形态（R18）：经 ToolRegistry.get 的文件解析链构造并注册
            # （基准目录显式——插件包目录等，不经 Agent source_dir 链）；
            # get 内部已按目录派生注册命名空间，显式 namespace 时覆盖之
            tool = self.tool_registry.get(tool)   # 文件 → Tool（惰性解析链）
            if namespace is None:
                return   # get 已按目录派生注册（§7a），不再二次注册
        self.tool_registry.register(tool, namespace=namespace)   # ns::name 全键冲突时后注册者报错（由 ToolRegistry.register 承载）

    def register_agent_type(self, name: str, agent_class: type[Agent], *,
                            namespace: str | None = None) -> None:
        """注册子 Agent 类型（``ns::注册名`` → Agent 类），使任何 Agent 可以引用。

        功能与动机：插件三类资源注册之一；注册表条目同样**惰性**——
        ``get_agent_class`` 在 invoke/创建时才解析类。注册表 key 为
        ``ns::注册名``；名称格式（kebab/snake/Pascal）由框架自动转化。

        行为边界：``ns::name`` 全键冲突时后注册者报错；注册只在
        ``install``。命名空间语义见模块 docstring §7a：``namespace`` 缺省
        落入 ``default::``（裸名视图优先层，同名即覆盖核心内置类型）；
        建议（非强制）插件用自身注册名作命名空间（``myplugin::xxx``），
        自定义命名空间的类型只能以全限定名引用。

        .. rubric:: 使用示例

        .. code-block:: python

            class MyPlugin(Plugin):
                def install(self, runtime: Runtime) -> None:
                    runtime.register_agent_type("payment-agent", PaymentAgent,
                                                namespace="myplugin")
                    # 引用方需写 myplugin::payment-agent

        .. rubric:: 调用关系（审计）

        - 调用：无（写类型注册表）
        - 被调：各插件 ``install()``（时机：阶段一注册；见 ``flowing.plugins`` 可用注册通道清单与本方法示例）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent_class`、
            :class:`flowing.agent.Agent`
        """
        # ns::name 全键冲突时后注册者报错（注册表 = 类注解 _agent_types）
        key = f"{namespace or 'default'}::{name}"
        self._agent_types[key] = agent_class   # 条目惰性：get_agent_class 在 invoke/创建时才解析
        agent_class.registry_key = key   # 回写（与 Tool/Skill.registry_key 同构；文件派生注册点同律）

    def register_config_namespace(self, name: str, schema: Any) -> None:
        """声明扩展的配置命名空间（「谁负责校验」的声明，非访问控制）。

        功能与动机：框架只解析核心命名空间（``agent`` / ``runtime``），其余 key
        透传给经本方法声明的扩展；扩展对已声明命名空间有完全控制权（校验、默认
        值、类型转换——经 ``schema`` 表达，具体形态由扩展自定）。未注册命名空间
        静默保留，``get_config()`` 可自由读取。

        行为边界：多扩展注册同一命名空间 → ``ConfigNamespaceConflictError``；
        注册只在 ``install``（R3）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.register_config_namespace("i18n", I18nSchema)

        .. rubric:: 测试案例

        - 前置：两个插件注册 ``"i18n"`` → 期望：第二个
          ``ConfigNamespaceConflictError``。
        - 前置：config.yaml 含未注册命名空间 ``foo:`` → 期望：不报错，
          ``get_config("foo.bar")`` 可读。

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_config_namespaces``）
        - 被调：各插件 ``install()``（时机：阶段一注册；示例见 ``flowing.plugins`` 与 ``flowing.errors.ConfigNamespaceConflictError``）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_config`、
            :exc:`flowing.errors.ConfigNamespaceConflictError`
        """
        if name in self._config_namespaces:
            raise ConfigNamespaceConflictError(name)   # 多扩展注册同一命名空间
        self._config_namespaces[name] = schema   # 注册只是「谁负责校验」的声明，非访问控制

    def register_resource(self, name: str, instance: Any) -> None:
        """注册共享 Resource（name → 任意实例，不要求继承基类）。

        功能与动机：与 ``get_resource`` 读写对称；Resource 生命周期跨 Agent
        （GB 级索引、连接池等加载一次全局共享），与 provide/inject 的区分原则：
        **值跟随 Agent 生命周期 → provide/inject；跨 Agent 跨任务 → Resource**。

        行为边界：name 在 Runtime 内全局唯一，重复注册 →
        ``ResourceNameConflictError``；必须在启动根 Agent（``mount()``）前完成；
        框架不代管 instance 的析构。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.register_resource("kb", KnowledgeBase("./index/product-docs"))
            runtime.register_resource("order_db", DatabasePool(dsn))

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_resources``）
        - 被调：各插件 ``install()`` / 子项目 ``main()``（时机：``mount()`` 前注册完毕；见 ``flowing.plugins`` 注册通道清单）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_resource`、
            :exc:`flowing.errors.ResourceNameConflictError`
        """
        if name in self._resources:
            raise ResourceNameConflictError(name)   # name 在 Runtime 内全局唯一
        self._resources[name] = instance   # 须在 mount() 前完成；框架不代管析构


    def snapshot(self, *, keys: set[str] | None = None) -> RuntimeSnapshot:
        """一致性只读快照：Runtime 全局状态的观测入口。

        .. rubric:: 功能介绍

        返回 :class:`flowing.snapshot.RuntimeSnapshot`——节点表、插件清单、
        agent 池、provider 候选名、设置、资源的一次性只读视图。
        供测试断言、repl ``/snapshot``、serve ``GET /snapshot`` 使用。
        插件状态不在快照中（M-81：无命名空间挂载机制），经
        :meth:`get_plugin` 拿插件自己的只读 API 观测。

        .. rubric:: 设计动机

        观测走只读视图而非直接读内部 dict：内部可变对象与控制信号
        （``asyncio.Event`` 等）不暴露，快照是可序列化、可断言的稳定面。

        .. rubric:: 使用示例

        .. code-block:: python

            snap = runtime.snapshot()
            assert snap.agents["agent-xxx"]["loaded"] is True

        .. rubric:: 行为规约

        - 只读：修改返回对象不影响 Runtime；字段为拷贝或 Info 视图。
        - 一致性：单次调用内各字段取同一时刻的读值。
        - ``keys``（S3）：``None``（默认）收集全部切面；指定时只收集
          指定字段（其余为 ``None``）。需要多切面同一时刻一致 → 同一次
          调用传入全部所需 key。
        - **可序列化**：全部字段 JSON 可序列化（``GET /snapshot`` 直接
          序列化即消费点），见 :mod:`flowing.snapshot` 总括不变量。
        - 不暴露项：``_executions`` 的控制信号、``_provided`` 的值内容
          （凭证等敏感值绝不进入快照）。
        - 完整字段契约见 :mod:`flowing.snapshot` 模块级 docstring。

        .. rubric:: 测试案例

        - 前置：Runtime 已 mount 根 Agent → 操作：``snapshot()`` → 期望：
          ``nodes`` 含 ``runtime-0`` 条目（其 ``parent_id`` 为 ``None``）
          且根 Agent 条目的 ``parent_id`` 为 ``"runtime-0"``。

        .. rubric:: 调用关系（审计）

        - 调用：无（构造 ``RuntimeSnapshot`` 只读视图）
        - 被调：``flowing.interfaces.cli`` repl ``/snapshot`` 命令、``cmd_serve`` / ``cmd_web`` 的 ``GET /snapshot`` 端点、``cmd_test`` 冒烟断言（时机：各见 ``flowing.interfaces`` 包 docstring）

        .. seealso:: :meth:`flowing.agent.Agent.snapshot`、
            :class:`flowing.snapshot.RuntimeSnapshot`
        """
        def _want(name: str) -> bool:
            return keys is None or name in keys   # S3：None 收集全部切面；指定时只收集指定字段（其余为 None）

        # nodes：_nodes 的 NodeInfo 只读投影（类型取共享 ID 空间前缀——
        # runtime-/agent-/workflow-；parent_id 对 Runtime 自身为 None——链终点无父）
        nodes: dict[str, NodeInfo] | None = None
        if _want("nodes"):
            nodes = {
                node_id: NodeInfo(
                    type=node_id.split("-", 1)[0],
                    parent_id=getattr(node, "_parent_id", None))
                for node_id, node in self._nodes.items()
            }
        # agents：池注册表的 AgentInfo 投影（含 loaded 标记；created_at 存储形
        # 式为 ISO 字符串——state.jsonl 须 JSON 可序列化——此处还原 datetime）
        agents: dict[str, AgentInfo] | None = None
        if _want("agents"):
            agents = {}
            for agent_id, meta in self._agent_pool.items():
                raw_created = meta.get("created_at") or ""
                try:
                    created = (raw_created if isinstance(raw_created, datetime)
                               else datetime.fromisoformat(str(raw_created)))
                except ValueError:
                    created = datetime.fromtimestamp(0, timezone.utc)   # 缺省/旧数据兜底
                agents[agent_id] = AgentInfo(
                    agent_type=meta.get("agent_type", ""),
                    parent_agent_id=meta.get("parent_agent_id", ""),
                    created_at=created,
                    loaded=agent_id in self._nodes)
        return RuntimeSnapshot(
            nodes=nodes,
            plugins=([p.name for p in self._plugins.values()] if _want("plugins") else None),
            agents=agents,
            providers=(list(self.provider_registry._candidates) if _want("providers") else None),   # 仅候选名——实例化与否不进快照
            config_overrides=(dict(self._config_overrides) if _want("config_overrides") else None),   # 只读副本（set_config 覆盖层）
            resources=(list(self._resources) if _want("resources") else None),
        )   # _provided 的值内容绝不进入快照（凭证边界）

    def set_model_tags(self, path: str | Path) -> None:
        """加载模型标签映射文件（可选初始化步骤，``mount()`` 之前调用）。

        功能与动机：模型标签**单值化**（标签 → 单个模型条目，无候选列表、无
        fallback 链；「换模型」只能动态改 ``self.model`` 或 ``model_tag``）。
        用户级映射（``$FLOWING_CONFIG_HOME/model-tags.yaml``）优先于项目级
        （``@/model-tags.yaml``）；``FLOWING_MODEL_TAGS`` 环境变量最优先。
        旧名 ``tags.yaml`` / ``FLOWING_TAG_MAPPING_PATH`` 已废弃。

        行为边界：仅登记映射来源，解析发生在 ``self.model`` 求值时（现场求值，
        无缓存）；路径支持 ``@/`` 前缀规则；标签无映射 → 模型调用时直接报错
        （无 fallback）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_model_tags("@/model-tags.yaml")

        .. rubric:: 调用关系（审计）

        - 调用：无（仅登记映射来源，解析现场求值于 ``self.model``）
        - 被调：无框架内调用点（可选初始化步骤，子项目 ``main()`` 于 ``mount()`` 前调用；消费方为 ``flowing.model`` 标签解析）

        .. seealso:: :class:`flowing.model.ModelConfig`、
            :meth:`flowing.runtime.Runtime.resolve_path`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        # 仅登记映射来源（存储字段 = 类注解 _model_tags_path）；解析现场求值于 self.model，无缓存
        # （文件解析经 flowing.model.load_model_tags——S-03 裁决具名；P1-23：产物为
        # 标签→条目名映射，「条目名→ModelConfig」经 flowing.model.load_models 产物 join）；
        # 标签无映射 → 模型调用时直接报错（无 fallback，见 flowing.model）
        self._model_tags_path = resolved

    def set_models(self, path: str | Path) -> None:
        """指定 models.yaml 来源路径（可选初始化步骤，``mount()`` 之前调用）。

        功能与动机：与 :meth:`set_model_tags` 对称的 models 侧通道——默认
        ``$FLOWING_CONFIG_HOME/models.yaml``（``FLOWING_MODELS_PATH`` 环境变量
        重定向），本方法以编程方式覆盖来源。

        行为边界：仅登记来源（``_models_path``），解析发生在 ``self.model``
        求值时（``flowing.model.load_models``，现场求值、无缓存）；路径支持
        ``@/`` 前缀规则。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_models("@/models.yaml")

        .. rubric:: 调用关系（审计）

        - 调用：无（仅登记来源，解析现场求值于 ``self.model`` 解析链）
        - 被调：无框架内调用点（可选初始化步骤，子项目 ``main()`` 于 ``mount()`` 前调用；消费方为 ``flowing.model``）

        .. seealso:: :meth:`flowing.runtime.Runtime.set_model_tags`、
            :meth:`flowing.runtime.Runtime.resolve_path`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        # 仅登记来源（存储字段 = 类注解 _models_path）；models 读取现场求值于
        # self.model 解析链（flowing.model.load_models 消费，优先本路径，
        # 缺省默认/环境变量），无缓存
        self._models_path = resolved

    def set_providers(self, path: str | Path) -> None:
        """指定 providers.yaml 来源路径（可选初始化步骤，``mount()`` 之前调用）。

        功能与动机：与 :meth:`set_model_tags` 对称的 providers 侧通道——默认
        ``$FLOWING_CONFIG_HOME/providers.yaml``（``FLOWING_PROVIDERS_PATH``
        环境变量重定向），本方法以编程方式覆盖来源并**重建候选清单**。

        行为边界：登记来源并立即重建 ``provider_registry``（候选清单是构造期
        产物，``mount()`` 前调用可覆盖；构造期无 provider 实例化，重建安全）；
        路径支持 ``@/`` 前缀规则；凭证（api_key 等）随文件，安全边界见
        ``flowing.providers`` 包 docstring。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_providers("@/providers.yaml")

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.providers.load_provider_candidates``（时机：每次
          ``set_providers`` 调用重建候选清单）
        - 被调：无框架内调用点（可选初始化步骤，子项目 ``main()`` 于 ``mount()`` 前调用）

        .. seealso:: :meth:`flowing.runtime.Runtime.set_models`、
            :meth:`flowing.runtime.Runtime.set_model_tags`、
            :func:`flowing.providers.load_provider_candidates`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        self._providers_path = resolved   # 登记来源
        # 重建候选清单（ProviderRegistry = 候选清单 + 懒实例化表；构造期无
        # provider 实例化，重建不丢任何已实例化条目——安全）
        self.provider_registry = ProviderRegistry(load_provider_candidates(resolved))

    def set_persist_dir(self, path: str | Path) -> None:
        """设置持久化目录（可选初始化步骤，``mount()`` 之前调用）。

        功能与动机：目录内两类条目并列——① 每 agent 一 session 目录
        （``agent_id == session_id``，父子平级），内含文件：
        ``tree.jsonl``（一行一个 Message + tombstone 等变更记录行）、
        ``core.jsonl``（核心袋，框架私有）+ ``state.jsonl``（默认袋
        set/delete 行——框架核心键裸名、插件键带注册名前缀）与
        ``meta.json``（身份四键，JSON 整写，D12）；
        ② **全局命名空间文件**：``<namespace>.jsonl`` 一空间一文件
        （Runtime/插件级状态，``core`` 含已注册 agent id 名录——池 key 的
        唯一权威来源；显式删除 = 删 session 目录 + 删名录项的一次操作）。
        本方法只设定全局目录——文件的物理读写由各持有者的
        :class:`flowing.persistence.FileRecordStore` 实例执行
        （:mod:`flowing.persistence`），不经 Runtime 中转。

        行为边界：路径支持 ``@/`` 前缀规则；运行时切换目录不受支持（在
        ``mount()`` / ``create_agent()`` 之后调用的行为未定义，属用法错误）。
        调用本方法后、池扫描 / 首个 ``mount()`` 之前，框架重放全部全局
        命名空间文件（恢复持久值进内存）。

        .. rubric:: 调用关系（审计）

        - 调用：无（仅设定全局持久化目录；物理读写由
          :class:`flowing.persistence.FileRecordStore` 执行）
        - 被调：无框架内调用点（可选初始化步骤，子项目 ``main()`` 于 ``mount()`` 前调用）

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.runtime.Runtime.register_state`、
            :meth:`flowing.agent.Agent.register_state`
        """
        self._persist_dir = self.resolve_path(str(path))   # 覆写默认 <cwd>/.flowing；路径支持 @/ 前缀规则
        self._persist_dir.mkdir(parents=True, exist_ok=True)   # 显式设定即建目录（默认路径则推迟到首个 mount/create 才建，见 _ensure_persist_ready）
        # 重指全部已注册命名空间的存储后端到新目录（R-07：__init__ 已对默认
        # 路径做过一次引导，本方法须让既有视图改读新目录——重建 store、清
        # 内存持久值、标记未重放，随后 _bootstrap_persistence 统一重放）。
        # 前置约定：mount()/create_agent() 之前调用（之后调用行为未定义）；
        # 此刻各命名空间尚无业务写入（框架自身尚未写 core），重建安全
        for namespace, view in self._states.items():
            object.__setattr__(view, "_store", FileRecordStore(
                self._persist_dir / f"{namespace}.jsonl", merge_last_line=True))
            object.__setattr__(view, "_persisted", {})
            self._bootstrapped.discard(namespace)
        # 引导时序：本方法后、池扫描 / 首个 mount() 之前重放全部全局命名空间
        # （重放经各命名空间 RecordStore.replay()，逐行覆盖 defaults；重放完成
        # 后各命名空间 view._maybe_compact(force=True)——压缩三时点①的 Runtime
        # 侧落点，空袋跳过）；池扫描随之进行（_bootstrap_persistence）
        self._bootstrap_persistence()

    def register_state(
        self,
        namespace: str,
        defaults: dict[str, Any] | None = None,
    ) -> StateView:
        """声明一个**全局**持久化状态命名空间（Runtime/插件级）。

        .. rubric:: 功能介绍

        状态**不挂在任何 agent 名下**：写在持久化根目录的
        ``<namespace>.jsonl``（一空间一文件）。承载 Runtime/插件的
        全局状态——框架的 ``core`` 全局命名空间含已注册 agent id 名录
        与已安装插件清单；插件的全局登记类状态（如通信扩展的全局路由
        表）也走这里。**加载/派生时机完全由插件内部管理**（最终裁决）：
        核心只提供基础设施——声明、写透、引导重放（
        ``set_persist_dir`` 之后、池扫描之前）；何时读出持久值
        派生运行时结构是插件自己的事（懒重建、显式初始化方法均可），
        核心不提供 ``load`` 恢复回调（与 Agent 侧单袋化裁决一致：
        Agent 侧的派生重建走 ``after_recover`` 钩子）。

        .. rubric:: 设计动机

        per-agent 状态解决「智能体自己的状态」；但池名录、插件全局
        登记等状态**先于、独立于**任何 agent 存在——它们需要一个与
        per-agent 机制同构（写透/末行合并/压缩/defaults/fail fast
        全继承）但归属全局的通道。一空间一文件：互不影响压缩与崩溃
        截断边界。

        .. rubric:: 使用示例

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                self._routes = runtime.register_state(
                    "comm.routes", defaults={"peers": []})
            # 之后：runtime.state("comm.routes").peers = [...]
            # 派生结构（如内存路由索引）由插件自己择时从 _routes 重建

        .. rubric:: 行为规约

        - **幂等**：同命名空间 + 同定义重复声明 = 空操作返回同一视图；
          同命名空间不同定义 → 报错。``core`` / ``meta`` 保留。
        - **引导重放**：全局视图在引导重放（``set_persist_dir`` 之后、
          池扫描之前）完成后读见持久值；重放前读仅见 defaults（内存
          尚未装载，非闸门拦截——D5 删写闸门）。插件在 ``install()``
          中声明但**不写**全局状态（R1 的持久化版）。
        - 重放产物是裸持久值（JSON 纯数据）；派生运行时结构的重建
          时机与方式由插件内部管理，异常由插件自行处理。

        :param namespace: 全局命名空间名（建议带插件前缀，如
            ``"comm.routes"``——框架不参与命名，冲突按声明规则处理）。
        :param defaults: 可选键默认值表；不落盘，读出回退。
        :return: 全局 :class:`flowing.persistence.StateView`。

        .. rubric:: 测试案例

        - 前置：注册 ``"x"`` 并写 ``runtime.state("x").k = 1`` → 进程
          重启 → 期望：重放后 ``runtime.state("x").k == 1``。
        - 前置：两插件声明同命名空间不同定义 → 期望：后者报错。
        - 前置：``install()`` 中写全局状态（引导重放前，读仅见 defaults）
          → 期望：写透本身不报错（D5），但重放前写入的键会被引导重放
          覆盖——故约定 install 内不写（R1 的持久化版）。

        .. rubric:: 调用关系（审计）

        - 调用：无（声明全局命名空间并返回 ``StateView``）
        - 被调：各插件 ``install()``（声明全局命名空间，时机：阶段一，见本方法使用示例）；框架自身 ``core`` 命名空间登记（时机：未见规约）

        .. seealso:: :meth:`state`、:meth:`set_persist_dir`、
            :meth:`flowing.agent.Agent.register_state`
        """
        # 幂等判定：同命名空间 + 同定义重复声明 = 空操作返回同一视图；不同定义 → 报错
        # （spec 未具名异常类型——声明冲突属编程错误，用内置 ValueError）；
        # core / meta 为保留命名空间（框架在 __init__ 自登记 core，用户侧不同定义
        # 的重复声明即被「不同定义 → 报错」拦截）
        defaults = {} if defaults is None else dict(defaults)
        existing = self._states.get(namespace)
        if existing is not None:
            if existing._defaults == defaults:
                return existing   # 同定义重复声明：幂等返回同一视图
            raise ValueError(
                f"全局状态命名空间重复声明且定义不同：{namespace}"
                f"（已登记 defaults={list(existing._defaults)}，"
                f"本次 defaults={list(defaults)}）")
        # 路径基 = _persist_dir（默认 <cwd>/.flowing，S-37；引导时序约束：
        # 全局状态声明先于池扫描 / 首个 mount()）
        view = StateView(
            FileRecordStore(self._persist_dir / f"{namespace}.jsonl",
                            merge_last_line=True),
            defaults=defaults)   # 一空间一文件一 store（S-31）；持久值在引导重放前不可见（内存未装载）
        self._states[namespace] = view
        return view

    def state(self, namespace: str, *, strict: bool = True) -> StateView | None:
        """取已声明全局命名空间的状态袋视图。

        功能与边界：全局侧的主访问路径（对应
        :meth:`flowing.agent.Agent.state`）。``strict``（C-01 同构，对齐
        :meth:`get_plugin`）：``True``（默认）未声明即 ``KeyError``
        （fail fast 防 typo——直接用写法）；``False`` 未声明返回
        ``None``（探测写法，如观测插件探测某插件是否声明了某命名空间）。
        读写语义全部继承
        :class:`flowing.persistence.StateView` 的类级契约。

        .. rubric:: 调用关系（审计）

        - 调用：无（返回已声明命名空间的 ``StateView``）
        - 被调：``flowing.runtime.Runtime.create_agent`` 池注册步（``state("core")["agents"]`` 写透，管线第 7 步）；插件代码读写全局状态（时机：声明后任意时刻）

        .. seealso:: :meth:`register_state`
        """
        if strict:
            return self._states[namespace]   # 默认 fail fast 防 typo
        return self._states.get(namespace)   # strict=False：探测形态返回 None

    # ------------------------------------------------------------------
    # 配置合并与持久化引导（内部 API，R-06 / R-07 落实）
    # ------------------------------------------------------------------

    @staticmethod
    def _read_config_file(path: Path) -> dict[str, Any]:
        """读一层配置文件（缺失按空层处理——R-06 推测方案；内部 API）。"""
        if not path.exists():
            return {}
        data = YAML(typ="rt").load(path.read_text(encoding="utf-8"))
        return dict(data) if data else {}

    @staticmethod
    def _flatten_config(data: dict[str, Any], *, _prefix: str = "") -> dict[str, Any]:
        """嵌套 dict 摊平为点分 key（浅合并的落实粒度：叶子字段级覆盖——
        「同名 key 高优先级覆盖、未覆盖 key 沿用低优先级」；列表不递归，
        整列表覆盖。内部 API）。"""
        flat: dict[str, Any] = {}
        for key, value in data.items():
            dotted = f"{_prefix}{key}"
            if isinstance(value, dict):
                flat.update(Runtime._flatten_config(value, _prefix=f"{dotted}."))
            else:
                flat[dotted] = value   # 列表值不递归——整列表覆盖（§6）
        return flat

    def _merge_config_layers(self) -> dict[str, Any]:
        """优先级链浅合并（R-06 落实；内部 API）。

        低 → 高：框架推荐默认值（``_FRAMEWORK_CONFIG_DEFAULTS``）< 项目级
        （``@/config.yaml``）< 用户级（``$FLOWING_CONFIG_HOME/config.yaml``，
        默认 ``~/.flowing/``）。逐层摊平为点分 key 后 ``dict.update`` 叠加。
        env 层暂无 key 映射规约（模块 docstring §6 环境变量表中的
        ``FLOWING_*`` 均为路径/开关类，由各自消费点直读，不进合并链）；
        命令行层不进链——由调用方经 ``set_config`` 落 ``_config_overrides``
        表达（读取时最优先）。
        """
        config_home = Path(os.environ.get(
            "FLOWING_CONFIG_HOME", os.path.expanduser("~/.flowing")))
        merged: dict[str, Any] = {}
        for layer in (
            _FRAMEWORK_CONFIG_DEFAULTS,
            self._read_config_file(self.project_root / "config.yaml"),
            self._read_config_file(config_home / "config.yaml"),
        ):
            merged.update(self._flatten_config(layer))
        return merged

    def _bootstrap_persistence(self, *, _materialize: bool = True) -> None:
        """全局持久化引导（R-07 落实；内部 API）：重放全部未重放的全局
        命名空间 → 压缩三时点① → agent 池扫描。

        幂等：只重放未入 ``_bootstrapped`` 的命名空间（已重放的不重放——
        重放会用文件旧值覆盖尚未 drain 的内存写；D5 删写闸门后「未引导」
        判断的替代机制）；池扫描只补登记池中缺失的 id。
        调用点：``__init__`` 尾部（默认 persist 路径场景的重放点）、
        ``set_persist_dir``（重指存储后）、``use()`` 尾部（install 完成后）、
        ``_ensure_persist_ready``（首个 mount/create/recover 前兜底——
        覆盖默认路径下 post-init 注册的插件命名空间）。

        ``_materialize``：有新命名空间本次重放（其写透即将成为可能）时，
        是否把持久化根目录建出来。**必须为真**的担保：FileRecordStore
        惰性打开句柄时不建父目录——「默认路径 + 插件写全局状态 + 全程无
        agent」会在 drain 时 FileNotFoundError → store poison。唯二的例外
        是 ``__init__`` 尾部（只读引导：core 一个视图，重放它不代表任何
        业务写意图，不在 cwd 建默认目录——零持久化场景不污染工作目录）。

        .. rubric:: 调用关系（审计）

        - 调用：``RecordStore.replay``（每命名空间重放）；``StateView._maybe_compact``（压缩三时点①）；``self._scan_agent_pool``（每次调用收尾）
        - 被调：``Runtime.__init__``（``_materialize=False``）/ ``set_persist_dir`` / ``use`` / ``_ensure_persist_ready``
        """
        bootstrapped_now = False
        for namespace, view in self._states.items():
            if namespace in self._bootstrapped:
                continue   # 已重放：不重放（防文件旧值覆盖未 drain 的内存写）
            persisted = view._persisted
            for record in list(view._store.replay()):   # replay 是惰性生成器，须显式消费
                op = record.get("op")
                if op == "set":
                    persisted[record["key"]] = record["value"]
                elif op == "delete":
                    persisted.pop(record["key"], None)
                # 未知行形态（meta 已被 replay 吸收）静默跳过——与 Agent._restore 同口径
            self._bootstrapped.add(namespace)
            bootstrapped_now = True
            if persisted:
                # 压缩三时点①的 Runtime 侧落点（空袋跳过：无内容可压，
                # 避免引导即在磁盘建出仅有 meta 首行的空文件）
                view._maybe_compact(force=True)
        if bootstrapped_now and _materialize:
            # 新命名空间重放 = 其写透即将成为可能——此刻建好持久化根目录
            # （无新重放则不建：use() 无状态插件 / 重复引导不落盘）
            self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._scan_agent_pool()

    def _scan_agent_pool(self) -> None:
        """agent 池扫描（模块 docstring §9；内部 API）：以全局 ``core``
        名录为池 key 唯一权威来源，逐个开 session 目录重建
        ``{agent_id → 元数据}`` 注册表（实例不在扫描阶段创建）。
        幂等：已在池的 id 跳过。

        .. rubric:: 调用关系（审计）

        - 调用：``self._read_pool_meta``（每个待登记 id）
        - 被调：``self._bootstrap_persistence``（每次引导收尾）
        """
        core = self._states.get("core")
        if core is None or "core" not in self._bootstrapped:
            return   # core 未重放（读不到持久值）——随引导再行扫描
        agents = list(core.get("agents", []))
        session_dirs = dict(core.get("session_dirs", {}))
        for agent_id in agents:
            if agent_id in self._agent_pool:
                continue
            session_dir = self._load_session_dir(session_dirs.get(agent_id), agent_id)
            self._agent_pool[agent_id] = self._read_pool_meta(session_dir, agent_id)

    def _read_pool_meta(self, session_dir: Path, agent_id: str) -> dict[str, Any]:
        """从 session 目录的 ``meta.json`` 解析池元数据（扫描专用；内部 API）。

        ``meta.json`` 是身份四键（agent_type / parent_agent_id / created_at
        / args）的 JSON 整写文件（create 管线第 3b 步写入，D12）。**决策 7**：
        ``meta.json`` 缺失即失败（FileNotFoundError），不回退读旧
        ``state.jsonl``——历史 session（无 meta.json）无效。
        """
        path = session_dir / "meta.json"
        if not path.exists():
            raise FileNotFoundError(
                f"池扫描：名录中 {agent_id} 对应的 meta.json 缺失（{path}）"
                "——身份元数据不兼容（决策 7：历史 session 无效，不回退读旧 state.jsonl）")
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not isinstance(meta.get("args"), dict):
            raise ValueError(f"池扫描：{agent_id} 的 meta.json 形态损坏: {path}")
        meta["session_dir"] = self._store_session_dir(session_dir)   # 目录在 core 名录平行映射，meta.json 不含
        return meta

    def _ensure_persist_ready(self) -> None:
        """首个 mount/create/recover 前的持久化就位（内部 API）。

        建持久化根目录（默认路径推迟到真正的持久化动作才落盘，避免零
        持久化场景在 cwd 留下空 ``.flowing/``）→ core 名录写入已安装
        插件清单（§8 core 内容之一；此刻 persist 目录已就位，写透安全）→
        兜底引导 post-init 注册的命名空间（默认路径场景的插件命名空间）。

        .. rubric:: 调用关系（审计）

        - 调用：``self._bootstrap_persistence``（每次调用收尾）
        - 被调：``Runtime.mount`` / ``create_agent`` / ``recover_agent``（各自入口）
        """
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        core = self._states.get("core")
        if core is not None and "core" in self._bootstrapped:
            plugins = sorted(self._plugins)
            if core.get("plugins", []) != plugins:
                core["plugins"] = plugins   # 写透（merge_last_line 防膨胀）
        self._bootstrap_persistence()

    def resolve_path(self, path: str, *, source_dir: Path | None = None) -> Path:
        """路径前缀规则的执行器：``@/`` ``./`` ``../`` 绝对路径 → ``Path``。

        .. rubric:: 功能介绍

        框架核心层方法。前缀语义：``@/`` → ``project_root``；``./`` →
        ``source_dir``；``../`` → ``source_dir.parent``，多级 ``../../`` 逐级
        向上；绝对路径接受；**裸名不走本方法**（名称查找，仅指向框架内
        注册表的内置 Agent/工具/技能）。

        **根内相对不变量**（M-64 最终裁决）：解析结果越出
        ``project_root`` 合法（绝对路径或 ``../`` 逃逸均可），但对象
        （消息 / 快照 / 日志 / 错误的对外文本）中的路径表示分两种：
        根内一律根相对形式（``@/a/b``）；**根外保留绝对路径**（如
        ``/etc/x``——跨机共享语义本就只对项目内容成立，宿主环境
        路径如实呈现）。

        .. rubric:: 设计动机

        不引入 ``ProjectPath`` 类型——路径解析就是字符串前缀判断，避免与
        ``pathlib.Path`` 互操作复杂度。无默认扫描目录：资源引用一律显式路径 /
        glob。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.resolve_path("@/tools/search.py")
            runtime.resolve_path("./subagents/*", source_dir=agent_dir)

        .. rubric:: 行为规约

        - ``./`` / ``../`` 前缀且未提供 ``source_dir`` → 报错（调用方应传
          ``agent.source_file`` 所在目录，见 ``flowing.agent``）。
        - 适用面：``$`` 引用、``{% include %}``、``subagents:`` / ``tools:`` /
          ``skills:`` 等所有文件路径引用。
        - 适用边界：仅用于 **flowing 项目资源引用**（``.fya`` 的 ``$``、
          ``{% include %}``、``tools:`` / ``skills:`` / ``subagents:`` 等
          路径字段）；**不涉及 flowing 自身的配置读取**——框架配置
          （``providers.yaml`` / ``config.yaml`` / XDG 用户级配置）由
          宿主启动层直接读取，其值经 config / provide 注入系统，路径
          本身不进入任何对象（M-64 分层约定）。
        - 输入语法：``@/`` / ``./`` / ``../`` / 绝对路径（``is_absolute``
          判定，POSIX 前导 ``/`` 与 Windows 盘符 / UNC 均算），以及含 ``/``
          或反斜杠字符且不含 ``::`` 的普通相对路径（以 ``source_dir`` 为基准）；
          前缀判定中 ``\\`` 与 ``/`` 等价（``.\\`` / ``..\\`` 视同
          ``./`` / ``../``）；``~`` / ``~/...`` / ``~\\...``
          **永远按绝对路径触发**——经 ``os.path.expanduser`` 展开为家目录
          后按绝对路径规则处理
          （P5 裁决：「不写 ``~``」是编码规范约定，不是内置限制；
          词法细节的唯一来源是 :func:`flowing.paths.resolve_path`）。
        - 边缘情况：``@/`` 不带后续路径段时表示**根目录本身**——通用
          规则的自然结果（``project_root / ""`` 经 pathlib 吸收空段即
          ``project_root``），无需特判；裸 ``@`` 不带斜杠不命中任何
          形态，按裸名/普通相对路径处理（几乎必为笔误）。
        - 非行为：不做存在性检查（解析 ≠ 打开）；不做 glob 展开（展开由调用方）。

        :param path: 带前缀路径字符串。
        :param source_dir: 相对前缀的基准目录。
        :return: 解析后的 ``Path``。
        :raises ValueError: 相对路径（``./`` / ``../`` 或含 ``/`` / 反斜杠
            字符）缺少 ``source_dir`` 时。

        .. rubric:: 测试案例

        - 前置：``project_root=/proj`` → 期望：
          ``resolve_path("@/a/b") == Path("/proj/a/b")``。
        - 前置：``source_dir=None`` → 操作：``resolve_path("./x")`` → 期望：
          ``ValueError``。
        - 前置：``source_dir=/proj/ag`` → 期望：
          ``resolve_path("../../x", source_dir=...) == Path("/x")``
          （越出根合法，保留绝对路径；对象表示经
          :meth:`to_project_path` 时原样保留）。

        .. rubric:: 调用关系（审计）

        - 调用：:func:`flowing.paths.resolve_path`（委托，注入
          ``project_root``）
        - 被调：``flowing.runtime.Runtime.get_agent_class``（路径形态类型名定位，每次解析）；``flowing.parsable``（``$`` 引用 / ``{% include %}`` 解析，时机见其模块规约）；``flowing.plugins.workflow.resolve_workflow``（workflow 定义路径解析）

        .. seealso:: :meth:`flowing.runtime.Runtime.to_project_path`、
            :func:`flowing.runtime.resolve`
        """
        return _paths_resolve_path(
            path, project_root=self.project_root, source_dir=source_dir)   # 纯函数委托(词法唯一来源在 flowing.paths)

    def to_project_path(self, absolute: Path) -> str:
        """反向表示：绝对路径 → ``@/`` 前缀字符串（用于日志与错误提示）。

        功能与动机：与 ``resolve_path`` 互逆的显示层工具；项目内路径用 ``@/``
        表示更短更可移植。

        行为边界：根内路径 → ``@/`` 前缀字符串；**根外路径原样返回绝对
        路径字符串**（不报错——跨机共享语义只对项目内容成立，宿主
        环境路径如实呈现；M-64 最终裁决）；纯字符串运算，不触碰
        文件系统。

        .. rubric:: 测试案例

        - 前置：``project_root=/proj`` → 期望：
          ``to_project_path(Path("/proj/a")) == "@/a"``；
          ``to_project_path(Path("/etc/x")) == "/etc/x"``。

        .. rubric:: 调用关系（审计）

        - 调用：:func:`flowing.paths.to_project_path`（委托，注入
          ``project_root``）
        - 被调：无（框架内零引用；docstring 定位为日志与错误提示的显示层工具，属下游公共 API）

        .. seealso:: :meth:`flowing.runtime.Runtime.resolve_path`
        """
        return _paths_to_project_path(absolute, project_root=self.project_root)   # 纯函数委托

    async def shutdown(self) -> None:
        """优雅关闭：递归 destroy → 插件收尾 → 总线关闭 → ``_shutdown_event.set()``。

        .. rubric:: 功能介绍

        框架核心层方法。语义是「**请求关闭**」而非「同步等待全进程退出」——
        发信号后立刻返回，避免「关闭时又要 await 自己的关闭」的递归；善后流程
        在本方法内按上述顺序执行完毕。

        .. rubric:: 设计动机

        协作式关闭：不用 ``os._exit`` / 强制 kill（除非关闭本身卡死，那是应用
        层兜底）。信号处理（SIGINT/SIGTERM）→ 本方法 → ``await runtime`` 处被
        唤醒 → 进程退出。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime = await flowing.launch(path)
            await runtime                  # 阻塞至 shutdown
            # 另一 Task / 信号处理器中：
            await runtime.shutdown()

        .. rubric:: 行为规约

        - 内部顺序（不变量）：递归 destroy 所有 Agent（工作循环 Task 被取消，
          **记录保留**——session 不移除；各 Agent 的 tree/state 后端在
          其 ``destroy()`` 内排空关闭）→ 插件收尾 → **关闭全部全局状态
          视图**（``_states`` 各 ``StateView._close()`` = drain 排空 +
          停写任务——契约②排空屏障点；放在插件收尾**之后**，插件
          ``shutdown()`` 中仍可写全局状态）→ 通信总线关闭 →
          ``_shutdown_event.set()``。
        - 不变量：插件收尾与总线关闭均发生在 ``_shutdown_event.set()`` **之前**
          ——``await runtime`` 解除阻塞时所有善后已完成。
        - 幂等：重复调用安全（收尾流程已完成过的重复调用直接返回；
          已置位的事件重复置位无副作用）。
        - 空 Runtime（无 Agent）合法：直接跳到插件收尾。
        - 非行为：不删除任何 session 目录（池 key 到显式删目录才移除）；不等待
          ``await runtime`` 的 waiter 实际被调度唤醒。

        .. rubric:: 测试案例

        - 前置：运行中的 Runtime → 操作：``await shutdown()`` → 期望：
          ``await runtime`` 处解除阻塞，且此时所有 Agent 已 destroy、插件收尾
          已完成。
        - 前置：已 shutdown → 操作：再次 ``await shutdown()`` → 期望：正常返回。

        .. rubric:: 调用关系（审计）

        - 调用：各 Agent ``destroy``（递归，内部顺序第 1 步，见 ``flowing.agent.Agent.destroy``）；各插件 ``Plugin.shutdown()``（第 2 步，按 install 顺序逐个 await，单插件异常记日志后继续——尽力收尾路径；``CommPlugin.shutdown`` → 总线 ``_close``、``CronPlugin.shutdown`` → ``CronScheduler._stop``）；``self._shutdown_event.set()``（末尾）
        - 被调：``flowing.interfaces._install_signal_handlers``（SIGINT/SIGTERM → shutdown）；repl ``/exit`` / EOF（见 ``flowing.interfaces.repl.cmd_repl``）；``flowing.interfaces.serve.cmd_serve`` / ``cmd_test`` 退出路径

        .. seealso:: :meth:`flowing.runtime.Runtime.__await__`、
            :meth:`flowing.agent.Agent.destroy`
        """
        # 幂等闸：收尾流程已完成过（事件已置位）的重复调用直接返回——
        # destroy / 插件收尾 / 视图关闭均不重演（重演会对已关闭的
        # RecordStore 再提交压缩请求而报错）
        if self._shutdown_event.is_set():
            return
        for node in list(self._nodes.values()):   # 第 1 步：递归 destroy 所有节点（Agent 的工作循环 Task 被取消，session 记录保留；Workflow 节点级联销毁其子 Agent）
            if node is self:
                continue   # Runtime 自注册在 _nodes 中但无 destroy()——销毁循环跳过自身
            await node.destroy()
        # 第 2 步：插件收尾（S-05 裁决：按 install 顺序逐个 await
        # plugin.shutdown()；单插件异常记日志后继续——尽力收尾路径）
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception:
                _logger.exception(
                    "插件 %s 收尾异常，继续后续收尾", getattr(plugin, "name", "?"))
        # 第 3 步：关闭全部全局状态视图（drain 排空 + 停写任务——契约②
        # 排空屏障点；在插件收尾之后，插件 shutdown() 中仍可写全局状态）
        for view in self._states.values():
            if view._persisted:
                view._maybe_compact(force=True)   # 压缩三时点②的 Runtime 侧落点（请求随 _close 排空一并执行；空袋跳过——避免为零内容命名空间建出实体文件）
            await view._close()
        # 第 4 步：通信总线关闭（未见具名符号；发生在 _shutdown_event.set() 之前）
        self._shutdown_event.set()   # 末尾置位；幂等（重复置位无副作用）；空 Runtime 直接跳到此处

    def __await__(self) -> Generator[Any, None, None]:
        """使 ``await runtime`` 阻塞至 shutdown（进程存活语义）。

        .. rubric:: 功能介绍

        等价于 ``yield from self._shutdown_event.wait().__await__()``。与有无
        Agent 无关——空 Runtime（未 mount）同样有效，只等退出事件。

        .. rubric:: 设计动机

        「保持进程存活」与「关闭信号」解耦：CLI / 嵌入方统一 ``await runtime``；
        若 ``main()`` 返回后不做此 await，进程立即退出（mount 返回 ≠ 有活干）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime = await flowing.launch(path)
            await runtime                          # 阻塞直到 shutdown()

        .. rubric:: 行为规约

        - 解除阻塞时点：``_shutdown_event.set()`` 之后——此时 destroy / 插件收尾 /
          总线关闭已全部完成。
        - 非行为：不消费消息、不做周期任务（Runtime 自身无事件循环职责）。

        .. rubric:: 测试案例

        - 前置：无任何 Agent 的空 Runtime → 操作：``create_task(wait(runtime))``
          后 ``await shutdown()`` → 期望：waiter 正常完成。

        .. rubric:: 调用关系（审计）

        - 调用：``self._shutdown_event.wait()``（时机：每次 ``await runtime``，等价语义见功能介绍）
        - 被调：``flowing.interfaces.run.cmd_run`` / ``flowing.interfaces.serve.cmd_serve``（launch 后 ``await runtime`` 阻塞至 shutdown，见各 docstring）

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        yield from self._shutdown_event.wait().__await__()   # 阻塞至 shutdown() 末尾置位；空 Runtime 同样有效

    def get_agent_class(self, agent_type: str, *,
                        source_dir: Path | None = None) -> type[Agent]:
        """类型名字符串 → Agent 类（惰性解析）——Agent 类型的**唯一解析
        公开入口**。

        功能与动机：创建/恢复管线的第一步；三资源解析 API 统一为
        ``get*`` 单入口（用户裁决）——与 ``ToolRegistry.get`` /
        ``SkillRegistry.get`` 同构，命名与 :meth:`get_agent` /
        :meth:`get_node` 同族（原 ``_resolve_agent_class`` 提升为公开，
        供插件/用户代码取类对象）。形态判别委托
        :func:`flowing.paths.classify_ref`（词法唯一来源），三种引用形态：

        - **限定名**（含 ``::``，如 ``myplugin::payment-agent``）：**只查
          注册表**精确键，不走文件查找链（命名空间规则见模块 docstring
          §7a）；
        - **裸名**（如 ``payment``）：``source_dir`` 提供时**先走文件
          查找链**（相对 ``source_dir``——文件覆盖注册表）；``source_dir``
          缺省时跳过文件链。之后查注册表裸名视图——``default::`` 优先于
          ``builtin::``（插件覆盖原生行为的通道）。需要文件上下文的调用
          走 :meth:`flowing.agent.Agent.get_agent_class`（自动携带
          ``source_dir``）；
        - **路径形态**（``./`` / ``@/`` / glob）经 ``resolve_path`` 定位
          ``.fya`` 或手写 ``.py`` 后编译/加载（``@/`` 锚 ``project_root``
          无需 ``source_dir``；``./``/``../`` 缺省 ``source_dir`` 报错）；
          目录形态候选链
          ``AGENT.fya`` > ``agent.fya`` > ``<name>.agent.fya`` >
          ``<name>.fya``，探测循环委托 :func:`flowing.paths.probe_candidates`、
          首个存在者生效（目录存在但无任一候选 →
          ``KeyError``；链上顺序只是确定性裁决规则，不推荐同一链路真的同时
          存在多个候选文件）；同名 ``.fya`` 单文件与文件夹并存时**文件夹优先**；
          ``.fya`` 与手写子类同名并存时 ``.fya`` 优先并告警。两级惰性：父
          Agent 实例化时只记元信息，创建/invoke 时才加载类。
          文件解析产物的命名空间从所在目录派生（``@/`` 下相对、根外绝对，
          文件夹式取上层目录），仅作内部身份标识（§7a），引用写法不变。

        路径形态细则：

        - 指向手写 ``.py`` 文件时，模块内需**恰好一个 Agent 子类**（与
          Workflow 定义文件的约定同构）；零个 →
          :class:`flowing.errors.FormatError`；**多个 → 用
          ``路径::ClassName`` 形态消歧**（R21：左段含路径特征——``/`` /
          反斜杠 / ``.py`` 结尾——时按「文件::类名」解析，绕开
          「恰好一个子类」限制；与命名空间限定名 ``ns::name`` 的区分在
          ``flowing.paths.classify_ref`` 词法层完成）。
        - 目录候选链**只含 ``.fya``**——不接管手写类的目录组织（手写类
          的目录组织走标准 Python 包机制 + ``register_agent_type``）。

        行为边界：解析失败抛 ``KeyError``；身份名一律**推断**（路径文件名 /
        目录名、注册名、类名 ``__name__``），``.fya`` 或手写子类中写了
        ``name`` 仅作一致性断言——与推断值不符抛
        :class:`flowing.errors.NameMismatchError`；``class_name`` 推断规则
        见 :class:`flowing.agent.Agent`。

        .. rubric:: 调用关系（审计）

        - 调用：``self.resolve_path()``（时机：路径形态类型名定位，见功能介绍）
        - 被调：``flowing.runtime.Runtime.create_agent``（管线第 1 步，每次创建）；``flowing.runtime.Runtime.recover_agent``（管线第 2 步，每次恢复）；``flowing.agent.Agent.get_agent_class``（自动携带 ``source_dir`` 的门面委托）；插件/用户代码（取类对象的公开入口）

        .. seealso:: :meth:`flowing.runtime.Runtime.register_agent_type`、
            :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.tool.ToolRegistry.get` —— 同构的 Tool 解析入口
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/order-agent::payment"），过不了 classify_ref 的
        # 限定名判别（左段含 / 判成路径形态）——注册表在场证据优先于词法
        # 分流（与 ToolRegistry.get 同口径）
        if agent_type in self._agent_types:
            return self._agent_types[agent_type]
        # 形态判别委托 classify_ref（词法唯一来源）——注意不能用
        # `"::" in agent_type` 粗判：「文件::类名」（R21 消歧形态）也含 ::，
        # 但属路径形态（左段含路径特征时 classify_ref 判 "path"）
        form = _classify_ref(agent_type)
        if form == "qualified":
            # 限定名（ns::name）：只查注册表精确键，不走文件查找链
            if agent_type in self._agent_types:
                return self._agent_types[agent_type]
            raise KeyError(agent_type)
        if form == "bare":
            # 裸名：source_dir 提供时先走文件查找链（相对 source_dir——文件
            # 覆盖注册表）；缺省时跳过文件链
            if source_dir is not None:
                loaded = self._load_agent_from_name_chain(agent_type, source_dir)
                if loaded is not None:
                    return loaded
            # 注册表裸名视图：default:: 优先于 builtin::（插件覆盖原生行为通道）
            for key in (f"default::{agent_type}", f"builtin::{agent_type}"):
                if key in self._agent_types:
                    return self._agent_types[key]
            raise KeyError(agent_type)
        # 路径形态（./ @/ 绝对路径 / 含分隔符的相对路径 / 文件::类名）：
        # @/ 锚 project_root 无需 source_dir；./ ../ 缺省 source_dir 报错
        # （resolve_path 现有口径）
        path_part, sep, class_name = agent_type.partition("::")
        resolved = self.resolve_path(path_part, source_dir=source_dir)
        return self._load_agent_from_path(
            resolved, class_name if sep else None, ref=agent_type)

    def _load_agent_from_name_chain(
        self, name: str, source_dir: Path
    ) -> "type[Agent] | None":
        """裸名的定向文件查找链（**内部 API**）。

        相对 ``source_dir`` 探测：目录形态 ``<name>/`` 优先（候选链只含
        ``.fya``：``AGENT.fya > agent.fya > <name>.agent.fya > <name>.fya``，
        首个存在者生效）；目录外依次 ``<name>.fya`` 单文件、
        ``<name_snake>.py`` 手写文件。``.fya`` 命中经
        :meth:`_load_agent_from_fya` 编译装配（parse_fya → 装配 → 合成）；
        ``.fya`` 与同名 ``.py`` 并存 → 告警且 ``.fya`` 优先。全部未命中 →
        ``None``（调用方继续查注册表裸名视图）。

        .. rubric:: 调用关系（审计）

        - 调用：``self._load_agent_from_fya``（.fya 命中时）/
          ``self._load_agent_from_py``（.py 命中时）
        - 被调：``Runtime.get_agent_class``（裸名且 source_dir 提供时）
        """
        directory = source_dir / name
        if directory.is_dir():
            hit = _probe_candidates(
                directory,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is not None:
                return self._load_agent_from_fya(hit, ref=name)
        fya_file = source_dir / f"{name}.fya"
        py_file = source_dir / f"{_kebab_to_snake(name)}.py"
        if fya_file.exists():
            if py_file.exists():
                warnings.warn(
                    f"同名 .fya 与手写 .py 并存，.fya 优先：{fya_file} / {py_file}")
            return self._load_agent_from_fya(fya_file, ref=name)
        if py_file.exists():
            return self._load_agent_from_py(py_file, None, ref=name)
        return None

    def _load_agent_from_path(
        self, resolved: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """路径形态的编译/加载（**内部 API**）。

        目录 → 候选链探测（只含 ``.fya``；无任一候选 → ``KeyError``）；
        ``.fya`` 文件 → :meth:`_load_agent_from_fya` 编译装配；
        手写 ``.py`` → :meth:`_load_agent_from_py`；不存在 / 其它后缀 →
        ``KeyError``（解析失败的统一口径）。

        .. rubric:: 调用关系（审计）

        - 调用：``self._load_agent_from_fya``（.fya 命中时）/
          ``self._load_agent_from_py``（.py 命中时）
        - 被调：``Runtime.get_agent_class``（路径形态）
        """
        if resolved.is_dir():
            name = _infer_name(resolved, naming=AGENT_NAMING)   # 目录：basename 即目录名
            hit = _probe_candidates(
                resolved,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is None:
                raise KeyError(ref)   # 目录存在但无任一候选 → 解析失败
            return self._load_agent_from_fya(hit, ref=ref)
        if not resolved.exists():
            raise KeyError(ref)
        if resolved.suffix == ".fya":
            return self._load_agent_from_fya(resolved, class_name, ref=ref)
        if resolved.suffix == ".py":
            return self._load_agent_from_py(resolved, class_name, ref=ref)
        raise KeyError(ref)

    def _load_agent_from_fya(
        self, path: Path, class_name: "str | None" = None, *, ref: str
    ) -> "type[Agent]":
        """``.fya`` 命中的编译装配与派生注册（**内部 API**）。

        经 :func:`flowing.compiler.compile_fya_class` 现场合成 Agent 子类
        （解析 → 装配 → 合成；``name`` 一致性断言在合成层完成）。派生注册
        与 :meth:`_load_agent_from_py` 同范式：派生键
        ``to_project_path(dir)::infer_name`` + ``registry_key`` 回写 +
        派生键已在注册表 → 短路复用（不重复合成）。``class_name``
        （``路径::ClassName`` 形态）对 ``.fya`` 仅作一致性断言——单文件
        只合成一个类，不符 → :class:`flowing.errors.FormatError`。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.compiler.compile_fya_class``（每次未短路命中）
        - 被调：``Runtime._load_agent_from_name_chain`` / ``_load_agent_from_path``
        """
        from flowing.compiler import compile_fya_class   # 局部 import：模块头依赖图保持单向（S-43）

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（通用名取目录名）
        derived_key = f"{self.to_project_path(path.parent)}::{name}"   # 目录派生命名空间（§7a）
        if derived_key in self._agent_types:
            cls = self._agent_types[derived_key]   # 派生键短路复用（文件解析是声明期行为）
            # 短路同样过 class_name 一致性断言——不得静默返回不符的已注册类
            if class_name is not None and cls.__name__ != class_name:
                raise FormatError(
                    f"{path} 已注册的合成类为 {cls.__name__}，与 {class_name!r} 不符"
                    "（.fya 单文件只合成一个类，:: 消歧是手写 .py 多类文件的机制）")
            return cls
        cls = compile_fya_class(path)
        if class_name is not None and cls.__name__ != class_name:
            raise FormatError(
                f"{path} 合成的类为 {cls.__name__}，与 {class_name!r} 不符"
                "（.fya 单文件只合成一个类，:: 消歧是手写 .py 多类文件的机制）")
        cls.registry_key = derived_key   # 回写（与 _load_agent_from_py 同构）
        self._agent_types[derived_key] = cls
        return cls

    def _load_agent_from_py(
        self, path: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """加载手写 ``.py`` 中的 Agent 子类（R-03；**内部 API**）。

        模块内需**恰好一个**本文件定义的 Agent 子类（``__module__`` 过滤掉
        import 进来的）；零个 → ``FormatError``；多个 → ``FormatError``
        （消息指明用 ``路径::ClassName`` 消歧，R21）；``class_name`` 指定时
        直接按名取（绕开「恰好一个」限制）。命中后注册到派生键（命名空间
        从所在目录派生：``@/`` 下根相对、根外绝对——``to_project_path``
        形式，仅作内部身份标识，§7a）并回写 ``cls.registry_key``；派生键
        已在注册表 → 短路复用（不重复加载）。**``::ClassName`` 消歧形态的
        派生键含类名**（``<目录键>::<身份名>::<类名>``——每类一键，否则
        同一多类文件先注册 ``::A`` 后 ``::B`` 会误短路返回 A）；无
        ``class_name`` 时为 ``<目录键>::<身份名>``。类体写了 ``name``
        仅作一致性断言：与推断值不符抛 ``NameMismatchError``。

        .. rubric:: 调用关系（审计）

        - 调用：``importlib.util`` 文件加载
        - 被调：``Runtime._load_agent_from_name_chain`` / ``_load_agent_from_path``
        """
        import importlib.util

        from flowing.agent import Agent   # 局部 import：模块头依赖图保持单向（S-43）

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（文件名去后缀、snake→kebab）
        base_key = f"{self.to_project_path(path.parent)}::{name}"   # 目录派生命名空间（§7a，内部身份标识）
        # ::ClassName 消歧形态的派生键含类名（每类一键）——同一多类文件
        # 先 ::A 后 ::B 时，B 不得误命中 A 的短路
        derived_key = f"{base_key}::{class_name}" if class_name is not None else base_key
        if derived_key in self._agent_types:
            return self._agent_types[derived_key]   # 派生键短路复用（文件解析是声明期行为）
        spec = importlib.util.spec_from_file_location(
            f"flowing_agent_file_{abs(hash(str(path)))}", path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(module)   # type: ignore[union-attr]
        if class_name is not None:
            cls = getattr(module, class_name, None)
            if not (isinstance(cls, type) and issubclass(cls, Agent)):
                raise FormatError(
                    f"{path} 内不存在 Agent 子类 {class_name}（文件::类名 消歧失败）")
        else:
            candidates = [
                obj for obj in vars(module).values()
                if isinstance(obj, type) and issubclass(obj, Agent)
                and obj is not Agent and obj.__module__ == module.__name__
            ]
            if not candidates:
                raise FormatError(
                    f"{path} 内没有 Agent 子类——手写 .py 需恰好定义一个")
            if len(candidates) > 1:
                raise FormatError(
                    f"{path} 内有多个 Agent 子类"
                    f"（{', '.join(c.__name__ for c in candidates)}）——"
                    "用 路径::ClassName 形态消歧（R21）")
            cls = candidates[0]
        explicit_name = cls.__dict__.get("name")   # name 非机制字段：写了仅作一致性断言
        if explicit_name is not None and explicit_name != name:
            raise NameMismatchError(
                f"{path} 中 {cls.__name__} 声明 name={explicit_name!r}，"
                f"与推断身份名 {name!r} 不符")
        cls.registry_key = derived_key   # 回写（与 register_agent_type 同构）
        self._agent_types[derived_key] = cls
        return cls

    def _resolve_session_dir(self, session_dir: str | Path | None, node_id: str) -> Path:
        """解析 agent 级 session 目录（**内部 API，不属稳定契约**）。

        ``None`` → ``persist_dir / node_id``（默认）；**绝对路径原样**；
        **相对路径以 ``runtime._persist_dir`` 为基准**解析（``persist_dir / p``）。
        供 ``create_agent`` 管线绑定 ``instance._session_dir``。
        """
        if session_dir is None:
            return self._persist_dir / node_id
        p = Path(session_dir)
        return p if p.is_absolute() else self._persist_dir / p

    def _store_session_dir(self, path: Path) -> str:
        """session 目录的**持久化存储形式**（内部 API，不属稳定契约）。

        ``persist_dir`` 内 → 相对形式（``os.path.relpath``，可移植——目录迁移
        后仍有效）；根外 → 绝对路径原样。与 :meth:`to_project_path` 的
        M-64 表示约定同构。
        """
        try:
            rel = os.path.relpath(path, self._persist_dir)
        except ValueError:   # 跨盘（Windows）无法 relpath
            return str(path)
        return rel if rel != "." and not rel.startswith("..") else str(path)

    def _load_session_dir(self, stored: str | None, node_id: str) -> Path:
        """从存储形式恢复 session 目录（内部 API，不属稳定契约）。

        空 → ``persist_dir / node_id``（兼容旧数据）；相对 → ``persist_dir / rel``；
        绝对 → 原样。供 ``recover_agent`` / 池扫描回绑 ``instance._session_dir``。
        """
        if not stored:
            return self._persist_dir / node_id
        p = Path(stored)
        return p if p.is_absolute() else self._persist_dir / p

    # ------------------------------------------------------------------
    # 内部方法（`_` 前缀）：以下签名承载关键时序，但不属于稳定契约。
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        """插件依赖图增量校验（成环抛错、缺失警告）。**内部 API，不属稳定契约**。

        功能与动机：R4「依赖只声明，检查是框架职责」的落实——与 mount
        **解绑**（R9 裁决：mount 只是根节点挂载，不承担校验职能）。
        每次 ``use()`` 安装后对**当前已装集合**的子图校验：

        - **成环** → 抛 :class:`flowing.errors.DependencyError`（报错
          现场即引入环的那次 ``use()``；``use()`` 可分批——缺依赖不报错，
          只有真成环才报）；
        - **依赖缺失** → ``warnings.warn`` 警告**不抛**（「声明了依赖但
          实际用不上」是合法形态；要严格化可用 ``-W error`` 升级）。
          运行时真用到缺失依赖时由 ``MissingProvideError``（inject
          失败）兜底。

        .. rubric:: 测试案例

        - 前置：``use(A)``（A 依赖未装的 B）→ 期望：警告，不抛。
        - 前置：``use(A)`` 后 ``use(B)``（A→B→A）→ 期望：第二次
          ``use`` 抛 ``DependencyError``（环）。

        .. rubric:: 调用关系（审计）

        - 调用：无（读 ``_plugins`` 的依赖声明做存在性 + DAG 无环校验）
        - 被调：``flowing.runtime.Runtime.use``（每次安装后，增量）

        .. seealso:: :meth:`flowing.runtime.Runtime.use`、
            :exc:`flowing.errors.DependencyError`
        """
        for plugin in self._plugins.values():
            for dep in plugin.dependencies:   # R4：插件只声明 dependencies: list[str]
                if dep not in self._plugins:
                    warnings.warn(f"插件依赖缺失：{plugin.name} 依赖未安装的 {dep}")   # 警告不抛（R9）
        # DAG 无环校验（DFS 三色标记；只走已装集合内的边——缺依赖已在上方警告，
        # 不成环）：已装子图成环 → DependencyError（报错现场 = 引入环的那次 use()）
        color = dict.fromkeys(self._plugins, 0)   # 0=未访问 1=在栈 2=完成

        def _visit(name: str, stack: tuple[str, ...]) -> None:
            color[name] = 1
            for dep in getattr(self._plugins[name], "dependencies", []):
                if dep not in self._plugins:
                    continue
                if color[dep] == 1:
                    # 成环：复用 DependencyError 的结构化字段表达——plugin 为
                    # 环闭合点所在插件，missing 列出构成环回边的依赖（spec 未
                    # 区分为缺/成环两种字段语义，就地裁决）
                    raise DependencyError(name, [dep])
                if color[dep] == 0:
                    _visit(dep, (*stack, name))
            color[name] = 2

        for name in self._plugins:
            if color[name] == 0:
                _visit(name, ())
