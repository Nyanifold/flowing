"""Payment tool (glob material #1 of the 5-1 complex assembly demo)."""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="Order ID")
    amount: float = Field(ge=0.01, description="Amount")


class PaymentTool(ScriptTool):
    """Initiate a payment (demo)."""

    name = "payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float) -> dict:
        return {"paid": order_id, "amount": amount}
