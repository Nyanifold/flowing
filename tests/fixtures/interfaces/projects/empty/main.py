"""阶段 5 接口层测试夹具项目：不 mount / 不创建任何节点的合法空项目。

``run`` 下合法空转（``await runtime`` 只等 shutdown）；``repl`` 下池空，
未绑定态收到消息打印「无法确定 Agent 类型」。
"""

from __future__ import annotations

from flowing import Runtime


async def main(persist: str | None = None) -> Runtime:
    runtime = Runtime()
    if persist:
        runtime.set_persist_dir(persist)
    return runtime
