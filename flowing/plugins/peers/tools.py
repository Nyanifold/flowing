"""Peers 的定向消息工具实现。

.. rubric:: 功能介绍

本模块提供 ``query-peer``、``message-peer`` 与 ``steer-peer`` 三个工具。
每次调用都从调用方的 provide 链取得路由服务，重新校验其 ``peers``
allowlist，并只向同一 Runtime 中一个精确 ID 的 Agent 投递
``MessageKind.PEER``。

.. rubric:: 行为要点

- ``query-peer`` 使用 async-generator 工具形态。第一个 yield 是立即返回的
  pending 收据；后续结果作为 EVENT 到达调用方。工具输出是普通字典，不
  直接返回 ``TurnResult``。
- ``message-peer`` 等待路由与入队完成，返回 ``peer_id`` 和 ``message_id``，
  不等待目标回合。
- ``steer-peer`` 与 ``message-peer`` 相同地返回消息 ID，但通过
  ``Agent.steer()`` 使用 STEER 优先级。
- 三件工具投递的文本统一以 ``FROM PEER <caller node id>:\n`` 前缀开头，
  目标 Agent 据此识别消息来源；前缀与 ``source`` 元数据并存。
- 目录错误发生在 query 收据之前。收据仅表示后台流程已接受，目标是否存在
  可能在其后的路由阶段失败。

.. seealso:: :mod:`flowing.plugins.peers.peers`、
    :class:`flowing.tool.Tool`、:class:`flowing.message.MessageKind`
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from flowing.agent import Agent
from flowing.message import MessageKind
from flowing.plugins.peers.peers import peers_router_key
from flowing.tool import Tool, ToolDefinition


def _peer_content(caller: Agent, content: str) -> str:
    """为投递文本添加 ``FROM PEER <caller node id>:`` 来源前缀。

    :param caller: 发起交互的 Agent。
    :param content: 原始投递文本。
    :return: 以 ``FROM PEER <caller node id>:\n`` 前缀开头的文本。
    """
    return f"FROM PEER {caller.node_id}:\n{content}"


class QueryPeerTool(Tool):
    """向单个 peer 发起异步 query，稍后以 EVENT 接收回合结果。

    首个 yield 作为 pending 收据。其后的后台阶段解析目标、等待目标
    ``Agent.query()``，再投递含 ``peer_status``、``response`` 与
    ``finish_reason`` 的结果事件。双向 query 不会在工具调用栈中互相同步
    等待；无界的 Agent 间追问由应用提示与逻辑自行约束。

    .. rubric:: 使用示例

    .. code-block:: yaml

        tools:
          - query-peer
        peers:
          oracle: "Answers questions about the hidden object."

    .. rubric:: 行为要点

    - ``peer_id`` 必须在调用方当前 ``peers`` 映射中声明。
    - 目标解析与 ``Agent.query()`` 均发生在首个收据之后；异步失败作为
      后台终止事件投递。
    - 投递的 ``prompt`` 以 ``FROM PEER <caller node id>:\n`` 前缀开头。
    - 消息使用 ``MessageKind.PEER``，``source`` 为
      ``peer_query:<caller node id>``。
    """

    definition = ToolDefinition(
        name="query-peer",
        description=(
            "Send a prompt to one declared peer agent. The call returns a receipt; "
            "the peer response arrives later as an event."
        ),
        params_schema={
            "peer_id": {"type": "string", "description": "Declared peer agent ID"},
            "prompt": {"type": "string", "description": "Prompt to send to the peer"},
        },
    )

    async def execute(
        self, peer_id: str, prompt: str, *, caller: Agent,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """先 yield 收据，再 yield 目标回合的结果摘要。

        :param peer_id: 调用方 ``peers`` 目录中的确切目标 ID。
        :param prompt: 投递给目标 Agent 的问题或请求。
        :param caller: Flowing 注入的发起 Agent。
        :yields: 首次 yield 为 accepted 收据；后续 yield 包含目标 ID、回合
            状态、最终文本与 finish reason。
        :raises ValueError: peer 未声明或目录无效时，在首次 yield 前抛出。
        """
        router = caller.inject(peers_router_key)
        router.validate_peer(caller, peer_id)

        yield {"accepted": True, "peer_id": peer_id}

        peer = await router.resolve_peer(caller, peer_id)
        result = await peer.query(
            _peer_content(caller, prompt),
            kind=MessageKind.PEER,
            source=f"peer_query:{caller.node_id}",
        )
        yield {
            "peer_id": peer_id,
            "peer_status": result.status,
            "response": result.final_text,
            "finish_reason": result.finish_reason,
        }


class MessagePeerTool(Tool):
    """向单个 peer 入队消息并立即返回消息 ID。

    工具等待目标解析及其 ``Agent.message()`` 入队完成，不等待目标回合或
    回复。消息以 ``MessageKind.PEER`` 进入目标 Agent 的队列与消息树。

    .. rubric:: 行为要点

    - 只接受调用方 ``peers`` 映射中声明的目标 ID。
    - 返回值包含 ``peer_id`` 和 ``message_id``。
    - 投递的 ``message`` 以 ``FROM PEER <caller node id>:\n`` 前缀开头。
    - 消息来源为 ``peer_message:<caller node id>``。
    """

    definition = ToolDefinition(
        name="message-peer",
        description=(
            "Send a message to one declared peer agent. The message is queued "
            "without waiting for the peer to respond."
        ),
        params_schema={
            "peer_id": {"type": "string", "description": "Declared peer agent ID"},
            "message": {"type": "string", "description": "Message to send to the peer"},
        },
    )

    async def execute(self, peer_id: str, message: str, *, caller: Agent) -> dict[str, str]:
        """解析目标并投递 PEER 消息，不等待目标回合。

        :param peer_id: 调用方 ``peers`` 目录中的确切目标 ID。
        :param message: 投递给目标 Agent 的消息文本。
        :param caller: Flowing 注入的发起 Agent。
        :return: 包含 ``peer_id`` 与目标 ``message_id`` 的字典。
        :raises ValueError: peer 未声明、目录无效或目标不在 Runtime 中时。
        """
        router = caller.inject(peers_router_key)
        peer = await router.resolve_peer(caller, peer_id)
        message_id = await peer.message(
            _peer_content(caller, message),
            kind=MessageKind.PEER,
            source=f"peer_message:{caller.node_id}",
        )
        return {"peer_id": peer_id, "message_id": message_id}


class SteerPeerTool(Tool):
    """向单个 peer 入队 STEER 指令，不等待目标回合或回复。

    工具调用 ``Agent.steer()``，以 ``MessagePriority.STEER`` 投递
    ``MessageKind.PEER``。目标回合不会被中断；导向内容在当前回合后续模型
    请求或下一回合中消费。

    .. rubric:: 行为要点

    - 只接受调用方 ``peers`` 映射中声明的目标 ID。
    - 返回值包含 ``peer_id`` 和 ``message_id``。
    - 投递的 ``instruction`` 以 ``FROM PEER <caller node id>:\n`` 前缀开头。
    - 消息来源为 ``peer_steer:<caller node id>``。
    """

    definition = ToolDefinition(
        name="steer-peer",
        description=(
            "Send a steering instruction to one declared peer agent. The instruction "
            "is queued with steering priority without interrupting its current turn."
        ),
        params_schema={
            "peer_id": {"type": "string", "description": "Declared peer agent ID"},
            "instruction": {
                "type": "string",
                "description": "Steering instruction to send to the peer",
            },
        },
    )

    async def execute(
        self, peer_id: str, instruction: str, *, caller: Agent,
    ) -> dict[str, str]:
        """解析目标并以 STEER 优先级投递 PEER 消息。

        :param peer_id: 调用方 ``peers`` 目录中的确切目标 ID。
        :param instruction: 投递给目标 Agent 的导向文本。
        :param caller: Flowing 注入的发起 Agent。
        :return: 包含 ``peer_id`` 与目标 ``message_id`` 的字典。
        :raises ValueError: peer 未声明、目录无效或目标不在 Runtime 中时。
        """
        router = caller.inject(peers_router_key)
        peer = await router.resolve_peer(caller, peer_id)
        message_id = await peer.steer(
            _peer_content(caller, instruction),
            kind=MessageKind.PEER,
            source=f"peer_steer:{caller.node_id}",
        )
        return {"peer_id": peer_id, "message_id": message_id}
