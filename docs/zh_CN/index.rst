Flowing
=======

**Flowing** 是一个面向复杂交互的轻量级、可扩展 Agent 运行时框架（Python ≥ 3.13）。它将单个智能体的全部定义收敛于一份 ``.fya`` 声明文件，将多个智能体的承载与组织交由一个可嵌入的运行时，并借助插件、Composable 与钩子开放行为扩展。消息与状态自动持久化，历史以可细粒度编辑的消息树组织。接入可按需展开：从一个 ``.fya`` 文件与 REPL 起步，逐步引入工具、子智能体、插件、Composable 与状态持久化，直至将运行时嵌入宿主应用。框架核心仅负责消息流转、错误分类与钩子点分发；重试、压缩与审批等策略以 Composable 或插件形式按需挂载。

它适合谁
--------

- **需要频繁审查、改动智能体的定义与逻辑**：声明、setup、钩子与类内逻辑都在同一份 ``.fya`` 文件里，工具与子智能体的绑定、可见性也逐条写在声明中，审阅时一眼看清它能用什么、模型能看见什么，改动不必在多个装配点之间往返；
- **希望把业务逻辑写进智能体本身**：智能体可自由定义属性与状态，钩子在各时点执行注入的代码——除接受、拦截或改写传入对象外，还会读写属性、更新状态、记录审计、查询缓存、向外部系统询问；
- **希望自己决定重试、压缩与审批这类策略**：核心只做消息流转、错误分类与钩子分发，这些策略以可读的 Composable 或插件外挂，可照抄、可改，也可以不启用；
- **希望以渐进式的路径构造智能体系统**：起步只需一个 ``.fya`` 加一份模型配置即可运行；此后每向复杂走一步——工具与子智能体、钩子逻辑、持久化状态、多智能体编排、嵌入宿主——都是在同一份 ``.fya`` 与普通 Python 代码上逐级累加，不引入新的机制层，也不需要更换工作方式；
- **需要把智能体嵌进既有进程，并自己写编排**：运行时是普通对象，持对象图，组织、共享依赖与下游编排都在普通 Python 代码里完成；
- **需要会话与状态跨运行延续，并能在事后编辑历史**：消息与状态自动持久化、恢复即重放；历史是消息树，可在任意消息上开分支、改写或裁剪。

这些需求并不指向某一种特定的应用形态：在 Flowing 中，编排逻辑就是普通的 Python 代码，顺序、分支与并发都由语言本身表达。因此，作为一个基础运行时框架，它可以用来构建编程、教育、电商、陪伴等各类智能体或多智能体系统，也可以用来开展多智能体交互实验。

核心特性
--------

- **语义自包含的智能体声明**：一个智能体的描述、模型意图、系统提示词、工具与子智能体、提供与注入的对象、下游插件所需的字段（如技能列表、联系目录）、钩子与 Composable 调用，均定义于同一个 ``.fya`` 文件，装配代码集中在其 ``setup()`` 中。同一工具、子智能体或技能只需定义一次，即可被多个智能体绑定为不同的模型视图，且注册不等于模型可见。因此，审阅单个智能体只需通读一个文件，无需在多个装配点之间往返。
- **可承载、可嵌入的运行时**：运行时是普通 Python 对象，可嵌入任意宿主程序。它承担多智能体的承载与组织，覆盖挂载、按 id 取用、恢复、销毁与归档；宿主或任一智能体均可从中获取子智能体，用于下游编排。数据库连接、索引等共享依赖在运行时上提供，并可注入任意深度的智能体。
- **插件、Composable 与钩子**：插件将整套扩展封装为可分发的单元，发布后可在其它项目中安装启用；工具、子智能体类型、技能与模型适配器同样可打包供其它项目引用。Composable 是更细粒度的复用单元：它是一个普通函数，将一段策略注入指定智能体，既可通过不同参数复用，也可在同一智能体内叠加。钩子挂载在消息循环的各个时点上，覆盖生命周期、回合、消息、模型调用、工具执行与子智能体各族；每个智能体均可自由定义自己的属性，并拥有自己的状态与运行逻辑。钩子函数在这些时点执行注入的代码：除接受、拦截或改写传入对象外，还可读写这些属性、更新状态、记录审计、查询缓存、向外部系统询问，等等。
- **消息与状态的自动持久化**：消息与状态自动落盘，进程崩溃后经重放重建。默认后端为 json/jsonl 文件，持久化后端可替换。待办事项、目标、Cron 计划、危险操作次数记录等需要跨运行保留的属性，声明为智能体的状态即可，其余由框架统一管理。
- **树状的消息结构**：对话历史以消息树组织。切换游标即可开出平行分支，旧分支完整保留；历史支持插入、分支、删除、改写与重挂五种操作，改动同样自动持久化。消息记录的结构因此可以自由组织与编辑。
- **内建的验证入口**：内置一次性 CLI、REPL、HTTP 服务与 Web 前端。即使前端还没搭好，自己搭建的智能体或智能体系统也能通过内建的入口直接用于演示、测试与验证。

安装
----

.. code-block:: bash

   pip install flowing-agent

快速上手
--------

这个示例会搭建一个双智能体猜词游戏：裁判 ``oracle`` 私下保管谜底，猜词者 ``guesser`` 通过定向消息逐步提问并猜出答案。它展示如何在一个 Runtime 中承载多个 Agent、由 ``PeersPlugin`` 注册 Peers 工具，并用声明式工具绑定控制 Agent 的工具可见性。工具按 ``peer_id`` 通过 Runtime 查询目标 Agent。两个 Composable 各司其职：``use_peers()`` 读取 ``peers`` 目录并将其注入系统提示词，``use_retry()`` 为模型调用配置重试，裁判使用默认设置（重试三次）而猜词者最多重试五次。

新建一个项目目录，并创建以下四个文件：

``main.py`` 安装 Peers 插件，并以稳定 ID 挂载两个 Agent：

.. code-block:: python

   from flowing import Runtime
   from flowing.plugins.peers import PeersPlugin

   async def main() -> Runtime:
       runtime = Runtime()
       runtime.set_model_tags("@/model-tags.yaml")
       # 注册 Peers 相关工具；Agent 是否能调用工具仍由各自的 tools: 声明决定。
       runtime.install(PeersPlugin())
       await runtime.mount("@/oracle.fya", agent_id="oracle")
       await runtime.mount("@/guesser.fya", agent_id="guesser")
       return runtime

``oracle.fya`` 描述知道谜底的裁判。它只允许向 ``guesser`` 发送消息：

.. code-block:: python

   description: "私下保管谜底，并判断猜词者的问题。"
   model_tag: default
   tools:
     - message-peer
   peers:
     guesser: "猜词者；通过私聊向你提问。请回复其答案。"

   ---
   $system_prompt:
   你是猜词游戏的裁判。用户会私下告诉你谜底。记住谜底，但不要向猜词者透露。
   猜词者发来问题时，根据谜底判断答案是肯定、否定还是无法判断。你必须调用
   message-peer 工具，将 peer_id 设为 "guesser"，并发送包含原问题及以下三种
   内容之一的消息：是、否、不确定。不要添加解释或其他文字，也不要直接回复猜词者。
   用户私下告诉你谜底时，只需简短确认设置完成，不要联系猜词者。

   ---
   $script:
   from flowing.composables import use_retry
   from flowing.plugins.peers import use_peers


   async def setup(self):
       # 注入 peers 目录提示，并启用默认的 Provider 调用重试策略。
       use_peers(self)
       use_retry(self)

       # status == "completed" 表示消息发送成功；本示例随即结束回合，不再请求 LLM 处理该工具结果。
       def _end_turn(agent, result):
           if result.status == "completed" and agent.current_turn is not None:
               agent.current_turn.finish = True
           return result

       self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")

``guesser.fya`` 描述通过提问推断谜底的猜词者：

.. code-block:: python

   description: "通过向裁判提问来猜出谜底。"
   model_tag: default
   tools:
     - message-peer
   peers:
     oracle: "知道谜底并回答是、否或不确定的裁判。请向其询问问题。"

   ---
   $system_prompt:
   你正在玩猜词游戏。当用户说明开始后，请每次向 oracle 智能体（裁判）提出一个可以用是或否回答的问题。
   不要要求裁判直接说出谜底。收到裁判回复后，根据游戏规则继续向该智能体提问；得到最终答案时，向用户
   给出你的最佳猜测。

   ---
   $script:
   from flowing.composables import use_retry
   from flowing.plugins.peers import use_peers


   async def setup(self):
       # 注入 peers 目录提示，并将单回合重试上限设为五次。
       use_peers(self)
       use_retry(self, max_retries=5)

       # status == "completed" 表示消息发送成功；本示例随即结束回合，不再请求 LLM 处理该工具结果。
       def _end_turn(agent, result):
           if result.status == "completed" and agent.current_turn is not None:
               agent.current_turn.finish = True
           return result

       self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")

``model-tags.yaml`` 将查询标签 ``default`` 映射到 Model 条目 ``luna``：

.. code-block:: yaml

   tags:
     default: luna

要让这两个 Agent 调用模型，先添加 OpenRouter Provider 并配置连接信息：

.. code-block:: console

   $ flowing-config providers add
   # Press Enter at the configuration path prompt to use the Runtime default.
   Provider entry name (identity): openrouter
   Provider adapter: openrouter
   API endpoint (base_url; leave blank to use 'https://openrouter.ai/api/v1'): [Enter]
   API key (api_key; leave blank to skip): env.OPENROUTER_API_KEY

Provider 配置完成后，再登记引用它的 Model 条目。示例将条目命名为 ``luna``，并指定 API model ID ``openai/gpt-6-luna``：

.. code-block:: console

   $ flowing-config models add
   # Press Enter at the configuration path prompt to use the Runtime default.
   Model entry name (identity): luna
   Known Provider entries: openrouter
   Provider entry name (identity): openrouter
   API model ID: openai/gpt-6-luna

``default`` model-tag 是查询标签，不携带 Provider 信息；它映射到 ``luna`` Model 条目，而条目再单独指定 ``openrouter`` Provider 和 API 模型 ID ``openai/gpt-6-luna``。adapter 参数提示依赖 API 模型 ID，不保证远端模型支持对应参数。

这里有两种不同的声明：``tools:`` 显式绑定 Agent 可调用的 ``message-peer`` 工具；``peers:`` 列出可联系 Agent 的 ID 与说明。``PeersPlugin`` 注册 Peers 工具；工具调用时按声明的目标 ID 通过 Runtime 查询目标 Agent。``use_peers(self)`` 这个 Composable 只读取当前 Agent 的 ``peers``，将目录动态注入系统提示词，不负责工具绑定。两个 Agent 都通过 ``use_retry()`` 启用 Provider 调用重试；裁判使用默认设置，猜词者使用 ``max_retries=5``。

运行：

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ flowing repl .
   (new agent)>>> /agent oracle
   (oracle)>>> 谜底是梨。请记住谜底，不要告诉猜词者。
   (oracle)>>> /agent guesser
   (guesser)>>> 开始游戏。谜底是一种水果。你最多可以提出五个是非问题，然后给出你的猜测。

``guesser`` 通过 ``message-peer`` 向 ``oracle`` 发送问题，裁判只返回“是”“否”或“不确定”；谜底留在裁判自己的会话中。两个 Agent 的消息记录都保存在项目目录下的 ``.flowing/`` 中，固定的 ``agent_id`` 让再次启动时可以恢复各自的历史。

.. note::

   REPL 的过程显示只处理前台绑定 Agent 由 Provider 产生的消息（流式正文、思考、工具调用与工具结果）和你的输入；运行中动态注入的消息不经此通道渲染——因此 ``message-peer`` 投递的 PEER 消息不会自动显示。输入 ``/messages`` 可查看当前消息链的完整消息列表（``/messages verbose`` 查看完整内容）。

进一步
------

- **能力接入**：``tools:`` 声明内置工具、MCP 服务，或将 shell 命令与 HTTP 接口声明为工具；自定义逻辑经 ``ScriptTool`` 实现。
- **多智能体**：``subagents:`` 声明子智能体类型，编排者依据自动生成的目录路由派单；也可以在代码中直接创建子 Agent 并行执行。``PeersPlugin`` 则让同一 Runtime 中的 Agent 通过 ``peers:`` 目录和消息队列定向交互。
- **运行介入**：在钩子点挂载 handler——工具执行前拦截待审批、回合收尾时审计、Provider 出错时换模型重试。内置的 ``use_retry`` / ``use_compact`` 即按此模式实现，可直接参考改写。
- **运行方式**：REPL、一次性 CLI、纯 HTTP API、内置 Web 前端、CI 冒烟测试等八个子命令，同一个项目无需改动即可用任意方式运行。
- **嵌入宿主**：``launch()`` 返回的 Runtime 由宿主持有，输入走 ``query`` / ``message`` / ``steer``，输出经钩子订阅（流式输出、完成通知、调用拦截）。

内置插件与 Composable
~~~~~~~~~~~~~~~~~~~~~~~

插件与 Composable 是两类可复用的扩展单元，均随包发布、按需启用。插件由 ``runtime.install()`` 安装，再由各 Agent 在 ``setup()`` 中调用对应的 ``use_xxx(self)`` 启用；Composable 是普通函数 ``use_xxx(agent, ...)``，在 ``setup()`` 中为单个 Agent 实例注入策略，可叠加，也可照抄改写。

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - 插件
     - 作用
   * - ``skills``
     - 按名加载一段提示词：``skills:`` 声明可用技能清单，LLM 经 ``skill-load`` 工具加载详细执行指南。
   * - ``comm``
     - 进程内通信总线：点对点信号（``send`` / ``request`` / ``reply``）与发布-订阅事件（``publish`` / ``subscribe``）两条通道，供 Agent、UI 与应用层代码协作。
   * - ``peers``
     - 同一 Runtime 内 Agent 的定向交互：注册 ``query-peer`` / ``message-peer`` / ``steer-peer``，按 ``peers:`` 目录限制可联系的对象。
   * - ``cron``
     - 逐 Agent 的定时任务：``schedule`` / ``unschedule`` / ``jobs`` 随 Agent 存续，LLM 经 ``schedule-cron`` / ``manage-cron`` 调度，到点经 ``on_cron_trigger`` 钩子投递。
   * - ``workflow``
     - 规则性编排：继承 ``Workflow`` 用 Python 显式写出分支、循环与并行/串行步骤，经 ``run-workflow`` 工具按路径拉起。
   * - ``clipboard``
     - 文件区段的剪切 / 复制 / 粘贴（``clipboard-cut`` / ``clipboard-copy`` / ``clipboard-paste``），用于跨文件搬运代码段。

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Composable
     - 作用
   * - ``use_retry``
     - LLM 调用抛出可重试异常时按退避模型等待后重发，至成功或达到次数上限。
   * - ``use_compact``
     - 请求后触发：上下文占用超阈值时让模型把当前对话压缩为交接摘要，摘要作为新根开新链。
   * - ``use_auto_compact``
     - 请求前触发：保留链头与链尾的原始消息，中段压缩为一条消息，本次请求携压缩后上下文发出。
   * - ``use_system_reminder``
     - 每个逻辑回合开始前注入一条系统提醒（当前时间、目录、任务状态等），随批次挂树并持久化。
   * - ``use_prompt_until``
     - 回合收尾运行断言，不成立时以 steer 消息导向下一个回合续跑。

文档
----

- :doc:`入门教程 <beginner-tutorial/index>`：面向 Agent 系统开发的初学者；
- :doc:`详细教程 <tutorial/index>`：从快速上手到核心机制与运维；
- :doc:`精简参考 <flowing-ref/index>`：四篇覆盖整个框架，适合查阅；
- :doc:`API 参考 <api>`：按模块组织的公开 API。

项目说明
--------

0.x 阶段仍可能有非兼容更新；每次此类更新都会附带详尽的更新日志并说明迁移方式，详见 `changelogs/ 目录 <https://github.com/Nyanifold/flowing/tree/main/changelogs>`__。

本项目在开发过程中高度依赖 AI 辅助生成，使用了来自不同提供商、不同能力强度的模型。受个人精力所限，作者未能逐行检查全部代码；若您发现实现与文档（docstring、教程）之间存在分歧，欢迎提出 `issue <https://github.com/Nyanifold/flowing/issues>`__ 指正。

License
-------

`MIT <https://github.com/Nyanifold/flowing/blob/main/LICENSE>`__ © 2026 Nyanifold

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: 文档

   beginner-tutorial/index
   tutorial/index
   flowing-ref/index
   api
