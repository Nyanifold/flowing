"""阶段 5 接口层测试夹具项目：main 在 launch 阶段失败。

- ``scenario="raise"``（默认）：直接抛 ``ValueError``。
- ``scenario="mount-missing"``：``mount("@/missing.fya")`` 不存在文件
  → launch 阶段原生异常上抛（``cmd_test`` / ``cmd_run`` 返回
  ``EXIT_RUNTIME_ERROR`` 的回归载体）。
"""

from __future__ import annotations

from flowing import Runtime


async def main(scenario: str = "raise", persist: str | None = None) -> Runtime:
    if scenario == "mount-missing":
        runtime = Runtime(persist_dir=persist)
        await runtime.mount("@/missing.fya")
        return runtime
    raise ValueError("main boom")
