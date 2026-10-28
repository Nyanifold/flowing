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

示例
--

这个示例把两个专用决策 Agent 嵌入 Python 应用：每个玩家返回结构化的产量选择，宿主并发查询两者并计算结果。游戏规则刻意保持简单，重点是可复用声明、live Agent 对象、实例级钩子与 Composable，以及由普通 Python 编排的可嵌入 Runtime。

新建一个项目目录，并创建 ``player.fya``、``main.py`` 和 ``model-tags.yaml``。

``player.fya`` 定义一个可复用的玩家声明：

.. code-block:: python

   description: "A player in a two-player Production Competition Game."
   model_tag: default
   output:
     quantity:
       type: integer
       enum: [3, 4]
       description: "The quantity to produce."

   ---
   $system_prompt:
   You are Player {{player}} in a two-player Production Competition Game.

   The selling price is 12 minus the combined production:
   price = 12 - (your quantity + the other player's quantity).
   Your profit = your quantity * price.

   Choose the quantity that maximizes your own profit.
   Return only a JSON object such as {"quantity": 3}.

   ---
   $script:
   from flowing.composables import use_retry
   from flowing.composables.validate import use_validate_turn_result
   from flowing.message import MessageKind


   async def setup(self, player: str):
       use_retry(self)
       use_validate_turn_result(self, on_fail="steer", max_retries=2)

       self.player = player
       self.requests = 0

       def log_request(agent, msg):
           if msg.kind is MessageKind.USER:
               agent.requests += 1
               print(
                   f"[{agent.node_id}] "
                   f"player={agent.player} requests={agent.requests}"
               )
           return msg

       self.hooks.on_enqueue(log_request)

声明将提示词、输出契约、启动逻辑、实例成员、钩子和 Composable 策略收拢在一起。``use_retry()`` 处理可重试的 Provider 异常；``use_validate_turn_result()`` 可以通过 steer 引导无效的结构化响应修正。钩子分别作用于每个挂载实例，只更新该实例自己的 ``requests`` 成员。

``main.py`` 将同一声明挂载两次，并把 Runtime 嵌入普通 Python 宿主：

.. code-block:: python

   import asyncio
   import json
   from pathlib import Path

   from flowing import Runtime, launch


   def payoff(q_a: int, q_b: int) -> tuple[int, int]:
       price = 12 - q_a - q_b
       return q_a * price, q_b * price


   async def main() -> Runtime:
       runtime = Runtime()
       runtime.set_model_tags("@/model-tags.yaml")
       await runtime.mount("@/player.fya", agent_id="player-a", player="A")
       await runtime.mount("@/player.fya", agent_id="player-b", player="B")
       return runtime


   if __name__ == "__main__":
       async def _run():
           runtime = await launch(str(Path(__file__).resolve().parent))
           try:
               a = await runtime.get_agent("player-a", strict=True)
               b = await runtime.get_agent("player-b", strict=True)
               result_a, result_b = await asyncio.gather(
                   a.query("Choose your production quantity: 3 or 4."),
                   b.query("Choose your production quantity: 3 or 4."),
               )
               q_a = json.loads(result_a.final_text)["quantity"]
               q_b = json.loads(result_b.final_text)["quantity"]
               profit_a, profit_b = payoff(q_a, q_b)

               a.last_profit = profit_a
               b.last_profit = profit_b
               print(f"Player A: quantity={q_a}, profit={a.last_profit}")
               print(f"Player B: quantity={q_b}, profit={b.last_profit}")
               print(f"Joint profit: {profit_a + profit_b}")
           finally:
               await runtime.shutdown()

       asyncio.run(_run())

``main()`` 是项目的装配入口：它创建、配置并**返回一个 Runtime**。``launch()`` 导入并等待 ``main()``，随后把这个 Runtime 返回给外部调用方。此后，宿主可以像使用普通 Python 对象一样直接使用它：通过 ``get_agent()`` 取得 live Agent，查询或更新这些对象，并用 ``asyncio`` 和应用逻辑自由编排。两次挂载拥有独立的属性、钩子和历史。这种嵌入模式既能承载产品中的领域专用 Agent，也能搭建可控的多智能体研究实验，无需引入工作流 DSL。

``model-tags.yaml`` 将查询标签 ``default`` 映射到 Model 条目 ``luna``：

.. code-block:: yaml

   tags:
     default: luna

标签本身不携带 Provider 信息：``default`` 解析到 ``luna``，再由条目分别指定 Provider 和 API 模型 ID。Provider 与 Model 的逐步配置见 `Hello Flowing <https://github.com/Nyanifold/flowing/blob/main/examples/hello-flowing/README.md#configure-the-luna-model>`_ 或 `Guessing Game <https://github.com/Nyanifold/flowing/blob/main/examples/guessing-game/README.md#configure-the-luna-model>`_。

在项目目录运行：

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ python main.py
   [player-a] player=A requests=1
   [player-b] player=B requests=1
   Player A: quantity=4, profit=16
   Player B: quantity=4, profit=16
   Joint profit: 32

具体选择由模型决定，因此数量与收益可能不同。逐步讲解见 `完整示例 <https://github.com/Nyanifold/flowing/tree/main/examples/product-competition-game>`_。

`examples 目录 <https://github.com/Nyanifold/flowing/tree/main/examples>`_按渐进顺序提供三个项目：`Hello Flowing <https://github.com/Nyanifold/flowing/tree/main/examples/hello-flowing>`_ 介绍项目装配、参数注入、``setup()`` 与持久化；`Guessing Game <https://github.com/Nyanifold/flowing/tree/main/examples/guessing-game>`_ 展示插件、Peer 消息和相互独立的 Agent 历史；`Production Competition Game <https://github.com/Nyanifold/flowing/tree/main/examples/product-competition-game>`_ 展示如何以可复用声明和 live Agent 对象支撑普通 Python 编排，并服务于专用智能体产品与研究项目。

进一步
------

- **能力接入**：``tools:`` 声明内置工具、MCP 服务，或将 shell 命令与 HTTP 接口声明为工具；自定义逻辑经 ``ScriptTool`` 实现。
- **多智能体**：``subagents:`` 声明子智能体类型，编排者依据自动生成的目录路由派单；也可以在代码中直接创建子 Agent 并行执行。``PeersPlugin`` 则让同一 Runtime 中的 Agent 通过 ``peers:`` 目录和消息队列定向交互。
- **运行介入**：在钩子点挂载 handler——工具执行前拦截待审批、回合收尾时审计、Provider 出错时换模型重试。内置的 ``use_retry`` / ``use_compact`` 即按此模式实现，可直接参考改写。
- **运行方式**：REPL、一次性 CLI、纯 HTTP API、内置 Web 前端、CI 冒烟测试等八个子命令，同一个项目无需改动即可用任意方式运行。
- **嵌入宿主**：``launch()`` 返回的 Runtime 由宿主持有，输入走 ``query`` / ``message`` / ``steer``，输出经钩子订阅（流式输出、完成通知、调用拦截）。

内置插件与 Composable
~~~~~~~~~~~~~~~~~~~~~~~

插件与 Composable 是随包发布的可复用扩展，默认均不启用。插件经 ``runtime.install()`` 注册工具、可注入服务等 Runtime 级能力；插件若提供配套 ``use_xxx(self)``，再在 ``setup()`` 中完成 Agent 级启用，所需工具仍须显式绑定。独立 Composable 是普通函数 ``use_xxx(agent, ...)``，无需 Runtime 安装即可为单个 Agent 挂载行为。Composable 可以叠加，其实现也可复制到应用层改写为所需策略。

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
     - 规则性编排：继承 ``Workflow`` 用 Python 显式写出分支、循环与并行/串行步骤，绑定 ``run-workflow`` 工具以按路径启动工作流。
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
   * - ``use_validate_turn_result``
     - 按显式 schema 或 Agent 的 ``output`` 校验最终回复 JSON；同回合 steer 修复（默认不限次数）、告警或抛错；有限修复次数耗尽时回合以 error 结束。
   * - ``use_validate_enqueued_message``
     - 按 kind、source 或谓词过滤入站消息，并按显式 schema 或 Agent 的 ``input`` 校验其文本或结构化数据；不合规时拒绝入队、告警或抛错。

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
