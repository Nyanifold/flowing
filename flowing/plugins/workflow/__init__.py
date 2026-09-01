"""``flowing.plugins.workflow`` —— 工作流编排扩展子包。

本包承载工作流编排扩展的全部公开符号：:class:`Workflow` （编排器抽象
基类，用户继承实现）、:class:`WorkflowPlugin` （阶段一插件）、
:class:`RunWorkflowTool` （``run-workflow`` LLM 触发工具）与
:func:`resolve_workflow` （按路径解析 workflow 定义文件）；对外 API 经
本 ``__init__`` 统一再导出。

.. seealso:: :mod:`flowing.plugins.workflow.workflow` （Workflow 基类与
    模块级编排契约）、:mod:`flowing.plugins.workflow.plugin` （工具与
    插件）、:mod:`flowing.plugins.workflow.loader` （定义文件解析）
"""

from flowing.plugins.workflow.loader import resolve_workflow
from flowing.plugins.workflow.plugin import RunWorkflowTool, WorkflowPlugin
from flowing.plugins.workflow.workflow import Workflow

__all__ = [
    "Workflow",
    "WorkflowPlugin",
    "RunWorkflowTool",
    "resolve_workflow",
]
