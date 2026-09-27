"""train-net 的 callable 实现——async generator 形态（B1/B5，不含 background 字段）。

呼应 ScriptTool 类 docstring“async generator 形态”的循环推回示例：
首 yield 收据 → 每 N 轮 yield 验证结果 → 末 yield 最终呈现。
"""


async def train_net(dataset: str, epochs: int = 4):
    """训练神经网络：首 yield 收据 → 每 2 轮 yield 验证结果 → 末 yield 最终呈现。"""
    yield {"status": "started", "dataset": dataset, "epochs": epochs}   # ① 收据
    for epoch in range(1, epochs + 1):                                   # 后台阶段
        if epoch % 2 == 0:
            yield {"epoch": epoch, "val_loss": 0.5, "val_acc": 0.8}      # ② 进度 → EVENT
    yield {"status": "done", "final_val_acc": 0.8}                       # ③ 最终呈现
