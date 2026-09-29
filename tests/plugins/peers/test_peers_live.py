"""Peers 两根 Agent 的真实 Provider 端到端冒烟测试。

网络门控：仅在显式设置 ``FLOWING_LIVE_TESTS=1`` 且提供
``DEEPSEEK_API_KEY`` 时运行。凭证从环境变量读取，不写入测试文件。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from flowing.message import MessageKind
from flowing.plugins.peers import PeersPlugin
from flowing.runtime import Runtime, _current_project_root


ROOT = Path(__file__).parents[3]
PROVIDER_FIXTURES = ROOT / "tests" / "fixtures" / "providers"

pytestmark = pytest.mark.skipif(
    os.environ.get("FLOWING_LIVE_TESTS") != "1"
    or not os.environ.get("DEEPSEEK_API_KEY"),
    reason="Live test requires FLOWING_LIVE_TESTS=1 and DEEPSEEK_API_KEY",
)


def _write_agents(root: Path) -> tuple[Path, Path]:
    oracle = root / "oracle.fya"
    oracle.write_text(
        """description: "Peer messaging oracle"
model_tag: fast
tools:
  - message-peer
peers:
  guesser: "The agent asking questions."
---
$system_prompt:
You know that the hidden object is a pear. When you receive a PEER message
from guesser, answer its question by calling message-peer with peer_id
\"guesser\" and a message containing only YES, NO, or UNKNOWN. Do not answer
the peer directly in assistant text.
---
$script:
from flowing.plugins.peers import use_peers

async def setup(self):
    use_peers(self)
""",
        encoding="utf-8",
    )
    guesser = root / "guesser.fya"
    guesser.write_text(
        """description: "Peer messaging questioner"
model_tag: fast
tools:
  - message-peer
peers:
  oracle: "The agent that knows the hidden object."
---
$system_prompt:
When the user gives you a question, send it to oracle exactly once using
message-peer. Do not answer it yourself. After the tool call, finish your turn.
---
$script:
from flowing.plugins.peers import use_peers

async def setup(self):
    use_peers(self)
""",
        encoding="utf-8",
    )
    return oracle, guesser


async def test_live_peer_message_roundtrip(tmp_path):
    token = _current_project_root.set(tmp_path.resolve())
    try:
        runtime = Runtime(persist_dir=tmp_path / ".flowing")
    finally:
        _current_project_root.reset(token)
    runtime.set_providers(PROVIDER_FIXTURES / "providers.yaml")
    runtime.set_models(PROVIDER_FIXTURES / "models.yaml")
    runtime.set_model_tags(PROVIDER_FIXTURES / "model-tags.yaml")
    runtime.install(PeersPlugin())
    oracle_path, guesser_path = _write_agents(tmp_path)

    oracle = await runtime.mount(str(oracle_path), agent_id="oracle")
    guesser = await runtime.mount(str(guesser_path), agent_id="guesser")
    replies = []
    replied = asyncio.Event()

    def capture_reply(agent, message):
        if (message.kind is MessageKind.PEER
                and message.source == "peer_message:oracle"):
            replies.append(message)
            replied.set()
        return message

    guesser.hooks.on_enqueue(capture_reply, by="live-test")
    try:
        result = await guesser.query("Ask oracle: Is the hidden object a fruit?")
        assert result.status == "completed", result.status
        await asyncio.wait_for(replied.wait(), timeout=90)
        reply_text = "".join(
            block.text for block in replies[0].content if hasattr(block, "text")
        )
        assert "YES" in reply_text.upper(), reply_text
    finally:
        await runtime.shutdown()
