"""参数与 setup() 演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime()
    # mount 之前 provide：影响装配的值在挂载前提供（1-5 讲链式语义）
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI 的 --key value 经 launch 原样透传 main(**kwargs)，
    # 再由 main 决定哪些参数交给 mount（→ 创建管线 → setup(**args)）
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
