from flowing import Runtime
from flowing.plugins.peers import PeersPlugin


async def main() -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(PeersPlugin())
    await runtime.mount("@/oracle.fya", agent_id="oracle")
    await runtime.mount("@/guesser.fya", agent_id="guesser")
    return runtime
