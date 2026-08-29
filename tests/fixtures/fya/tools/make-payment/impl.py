"""make-payment 的 callable 实现（显式指针通道，故意不打 @flowing_tool 标）。"""


async def make_payment(order_id: str, amount: float) -> dict:
    """对指定订单发起支付（函数 docstring 是 description 的最后回退）。"""
    return {"tx": "fake-tx", "order_id": order_id, "amount": amount}
