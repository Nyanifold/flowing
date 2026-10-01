"""``flowing.plugins.peers`` —— 同一 Runtime 内定向 Agent 交互扩展。

.. rubric:: 功能介绍

Peers 为 Agent 提供基于消息队列的定向交互。安装
``PeersPlugin`` 后，Runtime 注册 ``query-peer``、``message-peer`` 与
``steer-peer`` 三件工具；Agent 在 ``.fya`` 的 ``peers`` 字段声明可联系
的 Agent 及其说明，并在 ``setup()`` 中调用 ``use_peers(self)`` 注入目录。

Peers 与子 Agent 编排及 ``CommPlugin`` 相互独立。每次工具调用只路由到一
个同 Runtime Agent，内容以 ``MessageKind.PEER`` 进入目标消息队列和消息树；
发起 Agent 的私有历史不会复制给目标。

.. rubric:: 工具语义

- ``query-peer``：先返回 pending 收据，随后在后台等待目标 Agent 的
  ``TurnResult``，并把状态、最终文本与结束原因作为 EVENT 发送回调用方。
- ``message-peer``：入队 PEER 消息并返回消息 ID，不等待目标回合或回复。
- ``steer-peer``：以 STEER 优先级入队 PEER 消息并返回消息 ID，不打断
  目标当前回合。

所有工具都按精确 ``agent_id`` 路由到同一 Runtime，并在每次调用时重新
校验调用方的 ``peers`` allowlist。投递文本统一以
``FROM PEER <caller node id>:\n`` 前缀开头，目标 Agent 据此识别来源。
目标池记录存在但实例未加载时由 Runtime
恢复。``use_peers()`` 只注入目录，不绑定工具；Agent 仍须在 ``tools:`` 或
应用代码中显式绑定所需工具。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import Runtime
    from flowing.plugins.peers import PeersPlugin

    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(PeersPlugin())
    await runtime.mount("@/oracle.fya", agent_id="oracle")
    await runtime.mount("@/guesser.fya", agent_id="guesser")

``oracle.fya`` 与 ``guesser.fya`` 分别在 ``peers:`` 声明对方的稳定 ID，
在 ``tools:`` 只绑定所需工具，并在 ``$script`` 的 ``setup()`` 中调用
``use_peers(self)``。Composable 把 ID 与描述动态加入系统提示；描述按配置
原文显示，不作为 Parsable 模板再次求值。

Peer ID 必须是非空字符串，描述必须是非空字符串；目录必须非空且不能包含
Agent 自身的 ID。Composable 不要求目标已挂载，路由在工具调用时才检查。

.. seealso:: :mod:`flowing.plugins.peers.peers`、
    :mod:`flowing.plugins.peers.tools`、:mod:`flowing.message`
"""

from .peers import PeersPlugin, use_peers

__all__ = ["PeersPlugin", "use_peers"]
