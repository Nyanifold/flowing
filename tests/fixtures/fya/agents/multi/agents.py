"""multi/agents.py —— 多 Agent 子类文件（测试 64 的 ``文件::类名`` 消歧 fixture）。

模块内定义**两个**本文件 Agent 子类：无 ``::ClassName`` 消歧时
``get_agent_class`` 按“恰好一个”规则报错；``./multi/agents.py::PaymentAgent``
精确取类。两类均不声明 ``name``（不参与一致性断言）。
"""

from flowing.agent import Agent
from flowing.parsable import Parsable


class PaymentAgent(Agent):
    """多类文件中的支付 Agent（``::PaymentAgent`` 消歧目标）。"""

    system_prompt = Parsable("你是支付处理助手。")


class RefundAgent(Agent):
    """多类文件中的退款 Agent（``::RefundAgent`` 消歧目标）。"""

    system_prompt = Parsable("你是退款处理助手。")
