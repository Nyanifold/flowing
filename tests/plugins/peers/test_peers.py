"""Peers 插件的注册、目录、路由与工具语义。"""

from __future__ import annotations

import asyncio

import pytest

from flowing.errors import MissingProvideError
from flowing.message import MessageKind, MessagePriority
from flowing.plugins.peers import PeersPlugin, use_peers
from flowing.plugins.peers.peers import PeerRouter, peers_router_key
from flowing.plugins.peers.tools import MessagePeerTool, QueryPeerTool, SteerPeerTool

from harness import add_fake_provider, make_runtime, script_provider, text_response


async def _runtime(tmp_path, *, install=True):
    runtime = make_runtime(tmp_path, models=True)
    add_fake_provider(runtime)
    if install:
        runtime.install(PeersPlugin())
    return runtime


async def test_plugin_registration_does_not_bind_tools(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        agent = await runtime.create_agent("test-agent", agent_id="caller")
        assert agent._tool_entries == {}
        for name in ("query-peer", "message-peer", "steer-peer"):
            assert runtime.tool_registry.get(name).definition.name == name
        assert isinstance(runtime.inject(peers_router_key), PeerRouter)
    finally:
        await runtime.shutdown()


async def test_use_peers_injects_literal_dynamic_catalog_without_changing_tools(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        agent = await runtime.create_agent(
            "test-agent",
            agent_id="caller",
            peers={"peer-a": "Literal {{ 2 + 2 }} description."},
        )
        agent.add_tool("message-peer", alias="send-to-peer")
        agent.add_tool("steer-peer", alias="hidden-steer", body={"visible": False})
        entries_before = list(agent._tool_entries.items())

        use_peers(agent)

        assert list(agent._tool_entries.items()) == entries_before
        context = agent._assemble_context()
        catalog = next(segment.content for segment in context.system_prompt
                       if segment.name == "peers-catalog")
        assert "peer-a: Literal {{ 2 + 2 }} description." in catalog
        assert "`send-to-peer`" in catalog
        assert "hidden-steer" not in catalog
        assert "query-peer" not in catalog
        assert "continue to follow your own system instructions" in catalog
    finally:
        await runtime.shutdown()


async def test_use_peers_requires_plugin_and_valid_catalog(tmp_path):
    runtime = await _runtime(tmp_path, install=False)
    try:
        agent = await runtime.create_agent("test-agent", agent_id="caller")
        with pytest.raises(MissingProvideError):
            use_peers(agent)
    finally:
        await runtime.shutdown()

    runtime = await _runtime(tmp_path / "installed")
    try:
        agent = await runtime.create_agent("test-agent", agent_id="caller")
        invalid_catalogs = [
            None,
            {},
            {1: "Invalid ID"},
            {"peer-a": "   "},
            {agent.node_id: "Self"},
        ]
        for catalog in invalid_catalogs:
            agent.peers = catalog
            with pytest.raises(ValueError):
                use_peers(agent)
    finally:
        await runtime.shutdown()


async def test_message_and_steer_route_peer_messages(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        target = await runtime.create_agent("test-agent", agent_id="target")
        caller = await runtime.create_agent("test-agent", agent_id="caller")
        caller.peers = {target.node_id: "The target agent."}
        received = []
        target.hooks.on_enqueue(
            lambda agent, message: (received.append(message), message)[1],
            by="test",
        )

        message_result = await MessagePeerTool().execute(
            target.node_id, "Question", caller=caller)
        steer_result = await SteerPeerTool().execute(
            target.node_id, "Check this detail", caller=caller)

        assert message_result["peer_id"] == target.node_id
        assert message_result["message_id"] == received[0].id
        assert received[0].kind is MessageKind.PEER
        assert received[0].source == f"peer_message:{caller.node_id}"
        message_text = "".join(
            block.text for block in received[0].content if hasattr(block, "text"))
        assert message_text == f"FROM PEER {caller.node_id}:\nQuestion"
        assert steer_result["peer_id"] == target.node_id
        assert steer_result["message_id"] == received[1].id
        assert received[1].kind is MessageKind.PEER
        assert received[1].priority is MessagePriority.STEER
        assert received[1].source == f"peer_steer:{caller.node_id}"
        steer_text = "".join(
            block.text for block in received[1].content if hasattr(block, "text"))
        assert steer_text == f"FROM PEER {caller.node_id}:\nCheck this detail"
    finally:
        await runtime.shutdown()


async def test_query_peer_yields_receipt_then_turn_result_projection(tmp_path):
    runtime = await _runtime(tmp_path)
    provider = runtime.provider_registry.get("fake")
    try:
        target = await runtime.create_agent("test-agent", agent_id="target")
        caller = await runtime.create_agent("test-agent", agent_id="caller")
        caller.peers = {target.node_id: "The target agent."}
        received = []
        target.hooks.on_enqueue(
            lambda agent, message: (received.append(message), message)[1],
            by="test",
        )
        script_provider(provider, text_response("The peer answer."))
        generator = QueryPeerTool().execute(target.node_id, "Answer me.", caller=caller)

        receipt = await anext(generator)
        result = await anext(generator)

        assert receipt == {"accepted": True, "peer_id": target.node_id}
        assert result["peer_id"] == target.node_id
        assert result["peer_status"] == "completed"
        assert result["response"] == "The peer answer."
        assert isinstance(result["finish_reason"], str)
        query_text = "".join(
            block.text for block in received[0].content if hasattr(block, "text"))
        assert query_text == f"FROM PEER {caller.node_id}:\nAnswer me."
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
    finally:
        await runtime.shutdown()


async def test_query_peer_tool_returns_receipt_then_enqueues_result_event(tmp_path):
    runtime = await _runtime(tmp_path)
    provider = runtime.provider_registry.get("fake")
    try:
        target = await runtime.create_agent("test-agent", agent_id="target")
        caller = await runtime.create_agent("test-agent", agent_id="caller")
        caller.peers = {target.node_id: "The target agent."}
        caller.add_tool("query-peer")
        script_provider(
            provider,
            text_response("The peer answer."),
            text_response("I received the peer result."),
        )

        event_received = asyncio.Event()
        events = []

        def capture_event(agent, message):
            if message.kind is MessageKind.EVENT and message.source == "tool_result":
                events.append(message)
                event_received.set()
            return message

        caller.hooks.on_enqueue(capture_event, by="test")
        tool = runtime.tool_registry.get("query-peer")
        receipt = await tool(
            {"peer_id": target.node_id, "prompt": "Answer me."},
            caller=caller,
        )

        assert receipt.status == "pending"
        assert receipt.output == {"accepted": True, "peer_id": target.node_id}
        assert receipt.background_task_id is not None

        await asyncio.wait_for(event_received.wait(), 2)
        assert len(events) == 1
        assert events[0].content[1].data == {
            "peer_id": target.node_id,
            "peer_status": "completed",
            "response": "The peer answer.",
            "finish_reason": "end_turn",
        }
    finally:
        await runtime.shutdown()


async def test_undeclared_or_missing_peer_is_rejected(tmp_path):
    runtime = await _runtime(tmp_path)
    try:
        caller = await runtime.create_agent("test-agent", agent_id="caller")
        caller.peers = {"declared-but-missing": "No target is mounted."}
        router = runtime.inject(peers_router_key)

        with pytest.raises(ValueError, match="not declared"):
            router.validate_peer(caller, "other")
        with pytest.raises(ValueError, match="does not exist"):
            await router.resolve_peer(caller, "declared-but-missing")
    finally:
        await runtime.shutdown()
