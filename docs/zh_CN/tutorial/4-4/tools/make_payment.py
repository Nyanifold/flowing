"""支付工具（4-4）：三层能力描述的示例工具——同一工具，多副 LLM 面孔。"""
from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="订单 ID")
    amount: float = Field(ge=0.01, description="支付金额")
    currency: str = Field(default="USD", description="币种")
    user_id: str = Field(default="", description="操作人（由宿主注入，LLM 不可见）")


class MakePayment(ScriptTool):
    """对指定订单发起支付。仅在用户明确确认支付意图后调用。"""

    name = "make-payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float,
                      currency: str, user_id: str, caller: Agent) -> dict:
        # 打印实际收到的参数：LLM 给的 + specified 注入的，在此汇合
        print(f"[pay] execute 实参: order_id={order_id} amount={amount} "
              f"currency={currency} user_id={user_id!r} caller={caller.node_id}")
        return {"ok": True, "order_id": order_id, "amount": amount,
                "currency": currency, "user_id": user_id}
