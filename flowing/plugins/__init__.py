"""``flowing.plugins`` —— 扩展层包：``Plugin`` 统一基类与内置扩展。

.. rubric:: 功能介绍

本包承载 Flowing 三层架构的内置扩展层，包含两部分：

- :class:`Plugin` —— 所有扩展（含内置）的统一基类，唯一定义点；
  扩展作者继承本类并实现 ``install`` 即可接入框架。
- 随包发布、需显式启用的内置扩展，各自是一个独立子包：
  :mod:`flowing.plugins.skills`（``SkillPlugin`` / ``use_skill``）、
  :mod:`flowing.plugins.comm`（``CommPlugin`` / ``use_comm``）、
  :mod:`flowing.plugins.cron`（``CronPlugin`` / ``use_cron``）、
  :mod:`flowing.plugins.workflow`（``WorkflowPlugin`` / ``Workflow``）、
  :mod:`flowing.plugins.clipboard`（``ClipboardPlugin`` / ``use_clipboard``）。

启用遵循双层启用模型。阶段一调用 ``runtime.use(plugin)`` 安装全局能力
（工具、provide 值、配置命名空间、Agent 类型、Resource、全局状态命名
空间）；阶段二各 Agent 在 ``setup()`` 中调用 ``use_xxx(self)`` 做实例级
启用。框架核心发布时不预装任何内置扩展；未启用的扩展对 Agent 而言
从没存在过（零开销，不是被跳过）。``Plugin`` 基类定义在扩展包而非
``flowing.runtime``：核心只经 :meth:`flowing.runtime.Runtime.use` 消费
插件接口，不认识任何具体插件。

.. rubric:: 全局约定（跨符号、影响使用的约定）

- 生命周期：框架对插件实例只回调两个方法——安装时的
  :meth:`Plugin.install` 与关闭时的 :meth:`Plugin.shutdown`。其余运行期
  协作走 provide/inject 与消息队列，框架不主动调用插件的其它方法。
- 声明式依赖：插件在 ``dependencies`` 中声明依赖的其它插件注册名，
  只声明、不自己检查。每次 ``use()`` 安装后，框架对已装插件集合做
  增量校验：依赖缺失只发 ``warnings.warn`` 警告、不抛错；依赖成环抛
  :class:`flowing.errors.DependencyError`（报错现场即引入环的那次
  ``use()``）。
- 插件约定（consenting adults，靠自觉遵守而非框架校验）：R1
  ``install`` 只注册——不做业务、不查询其它插件、不修改其它状态；
  R2 协作不查询——插件间经 provide/inject 或消息队列协作，安装顺序
  与协作结果无关；R3 注册只在 ``install``——运行时不增删全局注册
  状态；R4 依赖只声明——不自己检查依赖是否满足。
- 绑函数约定（检查后跳过）：插件或 Composable 向 Agent 或 Runtime
  实例绑定函数成员（如 ``use_skill`` 绑定 ``agent.skill_load``、
  ``SkillPlugin.install`` 绑定 ``runtime.register_skill``）时，仅当
  对象当前没有该成员才绑定（``hasattr`` 检查，含类级方法）——开发者
  可能已自定义同名逻辑，绑定方不得覆盖。
- 命名约定（习惯约定，非强制校验）：插件注册名取「类名去掉 ``Plugin``
  后缀再转 kebab-case」（``CronPlugin`` → ``"cron"``、``SkillPlugin`` →
  ``"skill"``）；插件绑到 Agent 的成员以注册名的 underscore 版为前缀
  （``skill_load``、``comm_handler``），前缀即命名空间，因此不提供
  改名参数。
- 注册名是 per-Runtime 作用域的标签，不是全局唯一标识：生态上不排斥
  两个作用相近的插件取同一个注册名；约束只有一条——每个 Runtime 同时
  只装一个同名插件，重复安装同名插件时 ``use()`` 抛 ``ValueError``。
- 同名 provide key 重复注册是覆盖更新（后者生效，inject 实时可见），
  框架不报错；避免插件间键冲突靠键名前缀约定（插件注册名加 ``:``
  前缀）。
- 插件 ``install`` 抛出的异常从 ``use()`` 直接上抛，框架不按插件粒度
  隔离降级：安装失败的插件不会进入已装集合。

.. rubric:: 使用示例

.. code-block:: python

    from flowing.plugins import Plugin
    from flowing.runtime import Runtime


    class MyPlugin(Plugin):
        name = "myplugin"
        dependencies: list[str] = []   # 依赖其它插件时写其注册名

        def install(self, runtime: Runtime) -> None:
            runtime.register_tool(MyTool())            # MyTool 是你的工具类
            runtime.provide("myplugin:service", svc)   # svc 是你的服务对象

        async def shutdown(self) -> None:
            await svc.close()                          # 释放运行期资源

    runtime.use(MyPlugin())   # 阶段一：安装（每个插件恰好一次 install）

.. seealso:: :meth:`flowing.runtime.Runtime.use`（阶段一入口）、
    :meth:`flowing.runtime.Runtime.shutdown`（插件收尾阶段）、
    :class:`flowing.plugins.Plugin`、各内置扩展子包
"""

from __future__ import annotations   # 注解延迟求值：Runtime 仅经 TYPE_CHECKING 导入，避免插件包与 runtime 的注解级循环引用

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from flowing.runtime import Runtime

__all__ = ["Plugin"]


class Plugin:
    """扩展（插件）统一基类——内置扩展与用户扩展的共同接口。

    .. rubric:: 功能介绍

    阶段一（Runtime 安装）的契约载体：``runtime.use(plugin)`` 按实参顺序
    对每个插件调用一次 :meth:`install`，插件在此注册全局能力（工具、
    provide 值、配置命名空间、状态命名空间）。已废弃的
    ``PluginContext`` 中间对象与 ``FlowingPlugin`` 次级基类不再存在——
    ``install()`` 直接暴露 Runtime，本类是唯一基类。

    .. rubric:: 设计动机

    - **声明式依赖**：插件只声明 ``dependencies``，校验是框架的职责
      （``use()`` 时增量校验：成环抛 ``DependencyError``，缺失只警告
      不抛——R9 裁决，与 mount 解绑），取代旧的「``use()`` 顺序约定」。
    - **文档约定 R1–R4**（consenting adults，非结构强制）：
      R1 install 只注册（不做业务、不查询、不修改其它状态）；
      R2 协作不查询（插件间经 provide/inject 或消息队列协作，安装顺序无关）；
      R3 注册只在 install（运行时不增删全局注册状态；本版本不为热重载
      设例外，仅预留可能性，见模块 docstring 行为规约，M-03 裁决）；
      R4 依赖只声明（不自己检查）。
    - **绑函数约定（检查后跳过）**：插件 / Composable 向 Agent 实例
      **或 Runtime 实例**绑定函数成员（如 ``use_skill`` 绑
      ``agent.skill_load``；``SkillPlugin.install`` 绑
      ``runtime.register_skill``——R19 裁决：插件向 runtime 注入函数
      成员，核心不认识该插件）时，**仅当
      对象当前没有该成员才绑定**（``hasattr`` 检查，含类级方法，
      不只查实例 ``__dict__``）——开发者可能已自定义了同名逻辑
      （如自己的加载实现），绑定方不得覆盖。
    - **命名约定（两条，习惯约定非强制校验）**：
      ① 插件注册名 = 类名去 ``Plugin`` 后缀的 kebab-case：
      ``CronPlugin`` → ``cron``、``CommPlugin`` → ``comm``、
      ``SkillPlugin`` → ``skill``、``LoggingPlugin`` → ``logging``。
      ② 插件绑到 Agent 的成员（函数 / 属性）以注册名的
      **underscore 版为前缀**：``skill_load``（加载函数）、
      ``comm_handler``（通信句柄）。前缀即命名空间——撞名在约定
      层面不成立，因此**不提供改名参数**（改名会使下游失去规范
      发现通道，或被迫维护重命名表，均被否决）。
    - **注册名的生态语义**：注册名是 **per-Runtime 作用域**的标签，
      不是全局唯一标识——生态上**不排斥**两个作用相近的插件取
      同一个名字（如非官方的技能插件也可直接叫 ``skill``）；约束
      只有一条：**每个 Runtime 同时只装一个**同名插件（``use()``
      的同名冲突报错保证，见 :meth:`flowing.runtime.Runtime.use`）。
    - 获得 Runtime 全能力是有意为之：约束靠约定而非围墙，换取扩展作者
      的学习成本最小化（简单插件 = 只学注册）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.plugins import Plugin

        class GuardrailPlugin(Plugin):
            namespace = "guardrail"
            dependencies = ["flowing.plugins.comm"]

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(ApprovalTool())
                runtime.provide("guardrail:config", {...})
                runtime.register_config_namespace("guardrail", schema={...})

    .. rubric:: 行为规约

    - ``runtime.use(*plugins)`` 按实参顺序对每个插件调用一次 ``install``；
      可分批调用，依赖正确性不由顺序保障（由 ``mount()`` 时的 DAG 校验保障）。
    - 生命周期回调（S-05 裁决）：插件实例在 install 之后只被框架回调一次——
      ``shutdown()``（见 :meth:`Plugin.shutdown`）；其余运行期协作走
      provide/inject 与消息队列，框架不主动调插件的其它方法。
    - **install 对持久化状态的可反复执行性（与 ``Agent.setup`` 同构的
      契约）**：每次进程启动 ``main()`` 都重跑 ``use()`` → ``install()``，
      install 因此对持久化状态**结构上无关**、天然可反复执行——全局状态
      引导重放未发生（读到的只是 defaults）、install 期间写全局状态虽
      不报错（D5 删写闸门）但约定不写（重放前的写会被引导重放覆盖）；
      读持久值的派生重建一律放到
      ``after_recover``（agent 侧）或插件自择时机的懒重建（全局侧）。
      这与 ``Agent.setup`` 的「不同实例上每次调用作用相同、重放前只见
      defaults」是同一条逻辑的两端。
    - 边缘情况：``dependencies`` 中的名字无对应已安装插件 → ``use()``
      时 ``warnings.warn`` 警告不抛；依赖成环 → ``use()`` 抛
      :class:`flowing.errors.DependencyError`。
    - 前置条件：``install`` 在任何 Agent 创建（``mount``/``create_agent``）
      之前完成——否则阶段二 ``use_xxx()`` 的 ``inject`` 会失败。

    .. rubric:: 测试案例

    - 前置：``runtime.use(P())`` → 期望：``P.install`` 被调用恰好一次，
      实参为该 runtime。
    - 前置：插件声明 ``dependencies=["x"]`` 且未安装 ``x`` → 操作：
      ``runtime.use(P())`` → 期望：``warnings.warn`` 警告，不抛错。
    - 前置：两插件 A↔B 互相依赖 → 期望：第二个 ``use()`` 抛
      ``DependencyError``（成环）。

    .. rubric:: 调用关系（审计）

    - 调用：无（纯基类，自身不发起框架调用）
    - 被调：``flowing.runtime.Runtime.use()``（时机：阶段一每次
      ``runtime.use(plugin)``，按实参顺序对实例调用一次 ``install``）；
      内置实现 ``flowing.plugins.skills.SkillPlugin`` /
      ``flowing.plugins.comm.CommPlugin`` /
      ``flowing.plugins.cron.CronPlugin`` /
      ``flowing.plugins.workflow.WorkflowPlugin`` 均继承本类
    - 实例化方：无（框架内不实例化；由用户代码构造后传入
      ``Runtime.use()``）

    .. seealso:: :meth:`flowing.runtime.Runtime.use`、
        :meth:`flowing.runtime.Runtime._check_dependencies`（内部）、
        :class:`flowing.plugins.skills.SkillPlugin` 等内置实现
    """

    name: ClassVar[str]
    """插件名（注册名）。**子类必须显式声明，无框架默认与自动派生**。
    推荐格式（习惯约定，非强制校验）：类名去 ``Plugin`` 后缀的
    kebab-case（``CronPlugin`` → ``"cron"``）。用于依赖声明、
    日志与快照 ``plugins`` 列表；per-Runtime 作用域标签，生态
    语义见模块 docstring「注册名的生态语义」。
    
    .. seealso:: :attr:`namespace`
    """

    namespace: ClassVar[str]
    """插件命名空间（建议非强制）。约定注册名带前缀（``myplugin:xxx``）以避免
    跨插件冲突；显示名保持干净。provide key 同样建议带命名空间前缀。
    """

    dependencies: ClassVar[list[str]]
    """声明式依赖清单（其它插件的 ``name``/``namespace``）。R4：只声明，
    不自己检查；框架在 ``use()`` 时增量校验（成环抛错、缺失警告，R9）。
    
    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    @property
    def plugin_dir(self) -> Path:
        """插件自身所在目录（独立 Python 包的包目录）。

        插件随身携带资源文件（fya 工具 / 子 Agent 定义 / 模板等）时的
        路径基准——这些文件不在项目 ``@/`` 下、也不在任何 Agent 的
        ``source_dir`` 链上，只能由插件以自己的包目录拼路径：

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(
                    str(self.plugin_dir / "tools" / "pay.fya"),
                    namespace="myplugin",   # 显式命名空间覆盖目录派生（与文件路径无关）
                )

        实现：``Path(inspect.getfile(type(self))).parent``（插件类定义
        所在文件的目录）。只读、无副作用。
        """
        import inspect
        return Path(inspect.getfile(type(self))).parent

    def install(self, runtime: Runtime) -> None:
        """阶段一入口：向 Runtime 注册全局能力。每个插件被调用恰好一次。

        .. rubric:: 功能介绍

        可用的注册通道：``runtime.register_tool()``（**收实例或文件路径**——
        随身携带的资源文件以 :attr:`plugin_dir` 拼路径并显式给
        ``namespace``） /
        ``runtime.provide()`` / ``runtime.register_config_namespace()`` /
        ``runtime.register_agent_type()`` / ``runtime.register_resource()`` /
        ``runtime.register_state()``（全局命名空间开启；创建即 replay，
        加载/派生时机由插件内部管理）。
        （观测面无注册通道——插件状态经插件自己的只读 API 暴露，如
        ``runtime.get_plugin("cron").jobs()``；per-agent 状态键登记经
        ``agent.state.register()`` 在各 Agent 的 ``setup()`` 中完成，
        不在本阶段。）

        .. rubric:: 设计动机

        直接暴露 Runtime（取代 PluginContext 中间对象）：少一层间接，
        注册能力随 Runtime 演进自动可用，插件接口永不滞后。

        .. rubric:: 使用示例

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(SkillLoadTool())
                runtime.provide(skill_registry_key, SkillRegistry())

        .. rubric:: 行为规约

        - R1：只注册——不做业务、不查询其它插件、不修改其它状态。
        - R3：运行时不增删全局注册状态（本方法之外不再写注册表）。
        - 非行为：不返回任何值；不保存 runtime 引用用于运行时回调
          （运行期协作走 provide/inject 与消息队列）。
        - 边缘情况：重复 provide 同名 key → 后注册者报错（框架检测）。

        :param runtime: 当前安装的 Runtime 实例。

        .. rubric:: 测试案例

        - 前置：install 中 ``runtime.provide("k", v)`` → 期望：任意 Agent
          ``inject("k")`` 沿链上溯命中 Runtime 并返回 ``v``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.register_tool()`` /
          ``flowing.runtime.Runtime.provide()`` /
          ``flowing.runtime.Runtime.register_config_namespace()`` /
          ``flowing.runtime.Runtime.register_agent_type()`` /
          ``flowing.runtime.Runtime.register_resource()``（时机：阶段一
          install 内，每次安装；即上文列出的注册通道，均为按需可选）
        - 被调：``flowing.runtime.Runtime.use()``（时机：阶段一每次
          ``runtime.use(plugin)``，每个插件恰好一次）

        .. seealso:: :class:`Plugin`（R1–R4 约定）、
            :meth:`flowing.runtime.Runtime.provide`
        """
        # 基类本身无默认注册行为（空实现）；注册通道（register_tool /
        # provide / register_config_namespace / register_agent_type /
        # register_resource）由实现类按需选用，均为可选、非必经步骤。

    async def shutdown(self) -> None:
        """收尾入口：Runtime 优雅关闭时框架对每个插件调用一次（S-05 裁决）。

        .. rubric:: 功能介绍

        谁 install 谁收尾——插件显式管理自己的收尾逻辑（停定时器、关总线、
        释放运行期资源），默认 no-op。这是 install 之后框架**唯一**回调
        插件实例的方法。

        .. rubric:: 设计动机

        收尾是插件的内禀职责，不是需要另行注册的旁路回调（否决的替代
        方案：``Runtime.on_shutdown`` 回调注册表——多一张表、多一套
        顺序/异常策略，且收尾逻辑从插件类身上搬走）。

        .. rubric:: 行为规约

        - 时机：``Runtime.shutdown()`` 的插件收尾阶段，**晚于**所有 Agent
          的递归 destroy、**早于**通信总线关闭与 ``_shutdown_event.set()``
          （时序不变量）；按 ``use()`` 的 install 顺序逐个 ``await``。
        - 单插件异常不阻断后续收尾：框架记日志后继续（尽力收尾路径，
          一个插件的烂尾巴不应卡住别人）。
        - 非行为：不删持久化数据（session/state 随目录存续）；不做业务
          逻辑——只释放运行期资源。

        .. rubric:: 调用关系（审计）

        - 调用：插件自身的收尾方法（如 ``CronScheduler._stop`` /
          ``Communication._close``，由实现类按需）
        - 被调：``flowing.runtime.Runtime.shutdown()``（时机：插件收尾
          阶段，按 install 顺序，每次优雅关闭恰好一次）

        .. seealso:: :meth:`install`（阶段一对称端）、
            :meth:`flowing.runtime.Runtime.shutdown`
        """
        ...  # 默认 no-op；实现类覆写
