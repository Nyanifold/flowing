"""``flowing.plugins`` —— 扩展层包：``Plugin`` 统一基类与内置扩展。

.. rubric:: 功能介绍

本包承载 Flowing 三层架构的内置扩展层，包含两部分：

- :class:`Plugin` —— 所有扩展（含内置）的统一基类，唯一定义点；
  扩展作者继承本类并实现 ``install`` 即可接入框架。
- 随包发布、需显式启用的内置扩展，各自是一个独立子包：
  :mod:`flowing.plugins.skills` （``SkillPlugin`` / ``use_skill``）、
  :mod:`flowing.plugins.comm` （``CommPlugin`` / ``use_comm``）、
  :mod:`flowing.plugins.cron` （``CronPlugin`` / ``use_cron``）、
  :mod:`flowing.plugins.workflow` （``WorkflowPlugin`` / ``Workflow``）、
  :mod:`flowing.plugins.clipboard` （``ClipboardPlugin`` / ``use_clipboard``）。

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
  :class:`flowing.errors.DependencyError` （报错现场即引入环的那次
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

.. seealso:: :meth:`flowing.runtime.Runtime.use` （阶段一入口）、
    :meth:`flowing.runtime.Runtime.shutdown` （插件收尾阶段）、
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
    对每个插件实例调用一次 :meth:`install`，插件在此注册全局能力（工具、
    provide 值、配置命名空间、Agent 类型、Resource、全局状态命名空间）。
    插件实例由用户代码构造后传入 ``Runtime.use()``，框架不实例化插件。
    本类是扩展的唯一基类；子类须显式声明 :attr:`name` 与
    :attr:`dependencies` （:attr:`namespace` 可选），并按需实现
    :meth:`install` / :meth:`shutdown`。插件作者的约定（R1–R4、绑函数
    约定、命名约定）见本模块 docstring「全局约定」。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.plugins import Plugin
        from flowing.runtime import Runtime


        class GuardrailPlugin(Plugin):
            name = "guardrail"
            dependencies: list[str] = ["comm"]   # 依赖已安装的 comm 插件（缺失只警告不抛）

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(ApprovalTool())              # ApprovalTool 是你的工具类
                runtime.provide("guardrail:policy", policy)        # policy 是你的策略对象
                runtime.register_config_namespace("guardrail", {
                    "type": "object",
                    "properties": {},
                })

    .. rubric:: 行为要点

    - ``runtime.use()`` 按实参顺序对每个插件调用一次 ``install``；可分批
      调用，依赖正确性由每次 ``use()`` 后的增量校验保障（与安装顺序
      无关）；同名插件重复安装抛 ``ValueError`` （一个 Runtime 同时只装
      一个同名插件）。
    - 生命周期：插件实例在 ``install`` 之后只被框架回调一次——
      :meth:`shutdown`；其余运行期协作走 provide/inject 与消息队列，
      框架不主动调用插件的其它方法。
    - 推荐在创建任何 Agent（``mount`` / ``create_agent`` /
      ``recover_agent``）之前完成全部 ``use()``。框架不校验调用时机：
      迟装不报错，但已创建的 Agent 不会获得迟装插件注册的能力；插件
      未安装时，对应 ``use_xxx()`` 的第一步 ``inject`` 抛
      :class:`flowing.errors.MissingProvideError`。
    - install 的可反复执行性：每次进程启动都会重新执行 ``use()`` →
      ``install()`` （每个 Runtime 内每个插件只安装一次），install 应写
      成对持久化状态结构上无关——不读取持久值做注册决策；需要从持久值
      派生运行时结构的重建，放在 ``after_recover`` （agent 侧）或插件
      自择时机的懒重建（全局侧）。
    - 边缘情况：``dependencies`` 中的名字无对应已安装插件 → ``use()``
      时 ``warnings.warn`` 警告、不抛错；已装插件依赖图成环 → ``use()``
      抛 :class:`flowing.errors.DependencyError`。

    .. seealso:: :meth:`flowing.runtime.Runtime.use`、
        :class:`flowing.plugins.skills.SkillPlugin` 等内置实现
    """

    name: ClassVar[str]
    """插件注册名。子类必须显式声明：框架不提供默认值、不自动派生
    （``Runtime.use`` 直接按本名登记，未声明即 ``AttributeError``）。
    推荐格式（习惯约定，非强制校验）：类名去掉 ``Plugin`` 后缀转
    kebab-case（``CronPlugin`` → ``"cron"``）。

    本名用于：``dependencies`` 的依赖匹配（其它插件按注册名声明依赖）、
    ``runtime.get_plugin(name)`` 查询、日志、快照 ``plugins`` 列表，
    以及同名插件查重——一个 Runtime 同时只装一个同名插件。注册名是
    per-Runtime 作用域的标签，生态语义见模块 docstring「全局约定」。

    .. seealso:: :attr:`namespace`
    """

    namespace: ClassVar[str]
    """插件命名空间（建议非强制，框架不读取）。供插件自身资源的键名
    前缀约定：provide key、配置命名空间等以 ``"myplugin:xxx"`` 形态加
    前缀，避免跨插件冲突；对 LLM 可见的显示名保持干净（不带前缀）。
    """

    dependencies: ClassVar[list[str]]
    """声明式依赖清单：其它插件的注册名（``name``）列表。R4 约定：插件
    只声明、不自己检查；框架在每次 ``use()`` 后对已装插件集合做增量
    校验——清单中的名字无对应已安装插件时只发 ``warnings.warn`` 警告、
    不抛错；已装插件依赖图成环时抛
    :class:`flowing.errors.DependencyError` （报错现场即引入环的那次
    ``use()``）。

    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    @property
    def plugin_dir(self) -> Path:
        """插件自身所在目录：插件类定义所在文件的父目录。插件随身携带
        资源文件（``.fya`` 工具 / 子 Agent 定义 / 模板等）时的路径基准
        ——这些文件不在项目 ``@/`` 下、也不在任何 Agent 的
        ``source_dir`` 链上，只能以本属性拼路径传给
        ``runtime.register_tool()`` （文件路径形态）：

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(
                    str(self.plugin_dir / "tools" / "pay.fya"),
                    namespace="myplugin",   # 显式命名空间覆盖目录派生（与文件路径无关）
                )

        只读属性，无副作用。
        """
        import inspect
        return Path(inspect.getfile(type(self))).parent

    def install(self, runtime: Runtime) -> None:
        """阶段一入口：向 Runtime 注册全局能力。基类空实现，实现类按需
        选用注册通道。

        .. rubric:: 功能介绍

        可用注册通道（全部按需可选）：``runtime.register_tool()`` （收已
        构造的工具实例或工具定义文件路径——随身携带的资源文件以
        :attr:`plugin_dir` 拼路径并显式给 ``namespace``）、
        ``runtime.provide()``、``runtime.register_config_namespace()``、
        ``runtime.register_agent_type()``、``runtime.register_resource()``、
        ``runtime.register_state()`` （开启全局状态命名空间，创建即重放
        持久值；何时读取持久值派生运行时结构由插件自己管理）。观测面
        没有注册通道——插件状态经插件自己的只读 API 暴露（如 cron 扩展
        经 ``runtime.inject(cron_scheduler_key)`` 取调度器后调用
        ``jobs()``）；per-agent 状态键在 ``setup()`` 中经
        ``agent.state.register()`` 登记，不在本阶段。

        .. rubric:: 使用示例

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                # SkillLoadTool / skill_registry_key / SkillRegistry 来自 flowing.plugins.skills
                runtime.register_tool(SkillLoadTool())
                runtime.provide(skill_registry_key, SkillRegistry())

        .. rubric:: 行为要点

        - ``runtime.use()`` 按实参顺序对每个插件调用一次本方法；本方法
          抛出的异常从 ``use()`` 直接上抛（框架不隔离），安装失败的
          插件不会进入已装集合。
        - R1 约定：只注册——不做业务、不查询其它插件、不修改其它状态；
          R3 约定：注册只在本方法发生，运行时不增删全局注册状态。
        - 同 key 重复 ``provide`` 是覆盖更新（后者生效），框架不报错。
        - 返回 ``None``。运行期协作走 provide/inject 与消息队列，插件
          不在本方法保存 runtime 引用用于运行期回调。

        :param runtime: 当前安装的 Runtime 实例。

        .. seealso:: :meth:`flowing.runtime.Runtime.use`、
            :meth:`flowing.runtime.Runtime.provide`
        """
        # 空实现：注册通道由实现类按需选用，均为可选、非必经步骤。

    async def shutdown(self) -> None:
        """收尾入口：Runtime 优雅关闭时框架对每个插件调用一次。基类默认
        no-op，实现类覆写。

        .. rubric:: 功能介绍

        谁 install 谁收尾：插件显式管理自己的收尾逻辑（停定时器、关闭
        总线、释放运行期资源）。本方法是 install 之后框架唯一回调插件
        实例的方法。

        .. rubric:: 行为要点

        - 时机：``Runtime.shutdown()`` 的插件收尾阶段——晚于所有 Agent
          的递归 destroy，早于全局状态视图关闭与退出事件置位（此时
          ``await runtime`` 尚未解除阻塞）；按 ``use()`` 的 install
          顺序逐个 ``await``。
        - 单插件异常不阻断后续收尾：框架记日志后继续（尽力收尾路径）。
        - 不删除持久化数据：session 目录与全局状态文件随目录存续，本
          方法只释放运行期资源。

        .. seealso:: :meth:`install` （阶段一对称端）、
            :meth:`flowing.runtime.Runtime.shutdown`
        """
        ...  # 默认 no-op；实现类覆写
