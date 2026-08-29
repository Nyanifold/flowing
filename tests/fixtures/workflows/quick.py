"""函数形态 workflow 样例（T85）：顶层 async def run（无 self），
体内裸写 create_agent / agent / tool_call（编译期改写为 self.*）。

依赖宿主 Runtime 已注册 ``test-agent`` 类型与 ``echo-tool`` 工具
（测试侧负责注册）。
"""


async def run(prompt: str | None = None):
    first = await create_agent("test-agent")     # 裸名 → self.create_agent(...)
    second = await agent("test-agent")           # 简写 → self.create_agent(...)
    echo = await tool_call("echo-tool", text=prompt or "")   # → self.tool_call(...)
    await first.destroy()
    await second.destroy()
    return {"echo": echo.output}
