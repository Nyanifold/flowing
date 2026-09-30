Flowing
=======

`English <../en/>`__ | **中文**

Flowing 是一个为复杂交互设计的轻量级、可扩展的描述式 Agent 运行时框架（Python ≥ 3.13）。在 Flowing 中，一个 Agent 的全部定义，包括角色与提示词、大语言模型、工具与子智能体、Composable 扩展、钩子代码等，都写在同一个 ``.fya`` 文件里；框架在执行管线的关键时点向扩展开放，它的运行时亦可作为普通对象嵌入任意 Python 宿主应用。框架核心仅承担消息流转、错误分类与钩子点分发三项职责；重试、压缩、审批等策略均以 Composable 或插件形式按需挂载。

它适合谁
--------

- 希望框架的每一层行为都可读、可改、可审计，而不是面对一份黑盒的策略配置；
- 需要对会话历史做细粒度操作——在任意消息上开平行分支、改写或裁剪历史，而不只是追加式地对话；
- 需要同一种能力在不同 Agent 上呈现不同的模型视图，并要求“注册了不等于模型能看见”的显式安全边界。

这些需求并不指向某一种特定的应用形态：在 Flowing 中，编排逻辑就是普通的 Python 代码，顺序、分支与并发都由语言本身表达。因此，作为一个基础运行时框架，它可以用来构建编程、教育、电商、陪伴等各类智能体或多智能体系统，也可以用来开展多智能体交互实验。

核心特性
--------

- **消息驱动的运行模型**：每个 Agent 实例持有一条优先级消息队列和一个常驻工作循环；用户输入、模型响应、工具结果、外部事件、子 Agent 回执统一表示为消息，逐条驱动回合。``query()`` 等待回合结果，``message()`` 投递即返回，``steer()`` 在回合进行中导向。
- **消息级树的历史**：对话历史是由消息构成的森林，而不是线性列表。``fork()`` 只切换游标就能开出平行分支，旧分支完整保留；历史本身支持插入、分支、删除、改写、重挂五种手术操作，改动照常落盘。
- **三层能力描述**：可执行对象、LLM 可见声明、Agent 级绑定三个维度独立演化——同一个工具可以在不同 Agent 上呈现不同的名称、描述和参数视图。内置工具随运行时就绪，但必须显式声明才对模型可见。
- **声明式 .fya**：Agent 的描述、模型意图、能力绑定、系统提示词与钩子代码收进同一个自包含文件；编译产物与手写的 Agent 子类完全等价。
- **实例级钩子系统**：每个实例独立持有钩子注册表，钩子点覆盖生命周期、回合、消息、模型调用、工具执行与子 Agent 各族；handler 可以改写数据、异步等待外部确认，或抛出 ``Intercepted`` 硬阻断当前操作——人工审批闸由此自然实现。
- **自动持久化与崩溃恢复**：消息与状态以 write-behind 方式落盘，进程崩溃后重放重建；缺失配对的 tool_call 在恢复时自动封闭，模型看到的历史永远完整成对。
- **零代码工具接入**：MCP 服务（stdio / SSE / HTTP）、shell 命令模板（参数自动转义）、HTTP 接口，都可以用一份声明文件直接变成工具。
- **完整的暴露方式**：REPL、一次性 CLI、纯 HTTP API、内置 Web 前端、CI 冒烟测试等八个子命令，同一个项目无需改动即可用任意方式运行。

安装
----

.. code-block:: bash

   pip install flowing-agent

快速上手
--------

新建一个目录，写入五个文件。

``root.fya``：

.. code-block:: yaml

   description: 最小问答助手
   model_tag: default
   ---
   $system_prompt:
   你是一个简洁的中文助手，回答控制在三句话以内。

``main.py``：

.. code-block:: python

   from flowing import Runtime

   async def main() -> Runtime:
       runtime = Runtime(persist_dir="@/.flowing")
       runtime.set_model_tags("@/model-tags.yaml")
       await runtime.mount("@/root.fya", agent_id="agent-main")
       return runtime

使用全局命令登记 Provider 凭证和模型条目，再通过标签映射选择默认模型：

.. code-block:: console

   $ flowing-config providers add
   $ flowing-config models add

.. code-block:: yaml

   # model-tags.yaml
   tags:
     default: luna

运行：

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ flowing repl .
   (agent-main)>>> 用一句话介绍你自己。
   我是一个简洁的中文助手，回答会尽量控制在三句话以内。
   (agent-main)>>> /exit

这轮对话并没有随退出而消失：项目目录下的 ``.flowing/`` 里保存着刚刚产生的消息记录。``agent_id`` 固定意味着再次执行 ``flowing repl .`` 时，找回的是同一个 Agent 及其全部历史，无需额外的恢复代码。

全局 Provider 命令
------------------

安装 ``flowing-agent`` 后，可以用以下全局命令管理 Provider 条目；命令不启动 Runtime，也不访问模型服务::

   $ flowing-config providers add
   $ flowing-config providers list
   $ flowing-config providers delete

添加时逐项填写 adapter 字段；``env.API_KEY`` 会保存为 ``{{env.API_KEY}}``。列表会遮蔽密钥，但保留环境变量模板文本。删除操作只有明确输入 ``y`` 才会执行。配置改动在后续新建 Runtime 时生效。

全局模型命令
------------

独立命令也可以全局管理模型条目::

   $ flowing-config models add
   $ flowing-config models list
   $ flowing-config models delete

该命令不启动 Runtime，也不访问模型服务。添加时填写 Provider 条目名和 API 模型 ID，随后命令会
提供通用字段，以及 adapter 针对该模型 ID 声明的参数提示。这些提示不能保证远端模型
支持对应参数；用户仍可追加任意模型扩展字段，值使用单行 YAML。列表会遮蔽敏感字段。Agent 创建或重新指定
``model_tag``、Runtime 再次解析该模型条目时，配置改动生效。

进一步
------

- **能力接入**：``tools:`` 声明内置工具、MCP 服务，或将 shell 命令与 HTTP 接口声明为工具；自定义逻辑经 ``ScriptTool`` 实现。
- **多智能体**：``subagents:`` 声明子智能体类型，编排者依据自动生成的目录路由派单；也可以在代码中直接创建子 Agent 并行执行。``PeersPlugin`` 则通过 ``peers:`` 目录让同一 Runtime 中的 Agent 经消息队列定向交互。
- **运行介入**：在钩子点挂载 handler——工具执行前拦截待审批、回合收尾时审计、Provider 出错时换模型重试。内置的 ``use_retry`` / ``use_compact`` 即按此模式实现，可直接参考改写。
- **嵌入宿主**：``launch()`` 返回的 Runtime 由宿主持有，输入走 ``query`` / ``message`` / ``steer``，输出经钩子订阅（流式输出、完成通知、调用拦截）。

文档
----

- :doc:`入门教程 <beginner-tutorial/index>`：面向 Agent 系统开发的初学者；
- :doc:`详细教程 <tutorial/index>`：从快速上手到核心机制与运维；
- :doc:`精简参考 <flowing-ref/index>`：四篇覆盖整个框架，适合查阅；
- :doc:`API 参考 <api>`：按模块组织的公开 API。

项目状态
--------

当前版本 0.1.0。框架核心已完成（700+ 测试用例通过），目前处于示例场景工程阶段。0.x 阶段仍可能存在非兼容更新；每次此类更新都会附带详尽的更新日志，说明应当如何迁移。

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
