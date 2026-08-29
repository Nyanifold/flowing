"""payment 工具实现（order-agent fixture 的 TOOL.fya callable 目标）。"""


def make_payment(amount: float, currency: str = "CNY", user_id: str = "",
                 working_dir: str | None = None) -> dict:
    """模拟发起支付：返回交易收据（测试替身，无真实副作用）。"""
    return {"tx": "tx-demo", "amount": amount, "currency": currency,
            "user_id": user_id}
