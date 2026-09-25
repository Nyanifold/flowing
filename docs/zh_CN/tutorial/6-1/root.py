# 本文件由 flowing 编译器自动生成（compiler_version=0.1.3），来源：root.fya
# 请勿手工修改（py_hash 闸会拒绝覆盖外部修改）；如需手改请转正为手写子类。
from flowing import Agent, PENDING, Parsable


class RootAgent(Agent):
    source_file = '<仓库根>/dev-docs/tutorial/6-1/root.fya'
    description = Parsable('接口层演示助手：最小问答。')
    system_prompt = Parsable('你是简洁的中文助手。')
    model_tag = 'default'
