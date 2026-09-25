"""支付工具（5-1 复杂装配示例的 glob 素材之一）。"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="订单 ID")
    amount: float = Field(ge=0.01, description="金额")


class PaymentTool(ScriptTool):
    """发起支付（示例）。"""

    name = "payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float) -> dict:
        return {"paid": order_id, "amount": amount}
