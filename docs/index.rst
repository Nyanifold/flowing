flowing-agent 文档
==================

Flowing 是一个轻量式 Agent 框架，核心立场是
**“框架只提供机制，不提供策略”**——重试、压缩、审批等策略由扩展、
Composable 与应用层决定，核心负责错误分类、钩子点和消息流转。

文档分为三类：入门教程面向 agentic systems 开发入门者；详细教程
循序讲解 Flowing 的概念、机制与应用；精简参考按运行模型、消息树与
上下文、能力描述与多智能体、装配控制与运行环境四篇组织。API 参考
按包模块介绍公开接口。

.. toctree::
   :maxdepth: 2

   api/modules

索引
====

* :ref:`genindex`
* :ref:`modindex`
