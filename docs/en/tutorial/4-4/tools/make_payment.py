"""Payment tool (4-4): example tool for the three-layer capability description — one tool, many LLM faces."""
from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="Order ID")
    amount: float = Field(ge=0.01, description="Payment amount")
    currency: str = Field(default="USD", description="Currency")
    user_id: str = Field(default="", description="Operator (injected by the host, invisible to the LLM)")


class MakePayment(ScriptTool):
    """Make a payment for the given order. Call only after the user has explicitly confirmed the payment intent."""

    name = "make-payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float,
                      currency: str, user_id: str, caller: Agent) -> dict:
        # print the args actually received: what the LLM passed meets what specified injected
        print(f"[pay] execute args: order_id={order_id} amount={amount} "
              f"currency={currency} user_id={user_id!r} caller={caller.node_id}")
        return {"ok": True, "order_id": order_id, "amount": amount,
                "currency": currency, "user_id": user_id}
