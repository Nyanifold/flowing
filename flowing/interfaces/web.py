"""Flowing Web 暴露层规约（``flowing.interfaces.web``）。

本模块定义 ``web`` 子命令与 Web 暴露层的唯一交接点：
:func:`get_frontend_assets`。

.. rubric:: 定位：示例性下游应用，不是组件库

本模块**不是**通用 UI 组件库——在 flowing 的架构里，UI 完全是几个
「下游应用」的例子。``flowing.interfaces.web`` 承载的只是 ``web`` 子命令所需
的**内置默认前端资产**；它存在的意义是演示「用公开 API 能拼出一
个可用的 web 对话页」，而非提供可组合的界面构件。要真正的交互产
品，请以此为范例自建。

.. rubric:: 前端是策略，不是核心

框架核心只提供「HTTP API」这个机制（``flowing serve`` 已完整）。
「给不给前端、前端长什么样」是**暴露层的策略**，由本模块承载；
替换或移除前端不影响 :data:`flowing.interfaces.serve.SERVE_ENDPOINTS`
中的任何一条。``web`` 命令
只是「serve + 加载本模块前端资产」的便捷组合。

.. rubric:: 初版范围边界（X2/X4 裁决）

- 初版提供**框架内置的默认前端**：持续对话页面——经
  ``POST /agents/<agent-id>/message`` 发消息、经
  ``GET /agents/<agent-id>/stream``（SSE）**流式显示**生成过程
  （正在生成 → 流式 delta → 完整生成两段式；工具调用与 steer 注入
  经 ``message`` 事件可见）、可查看快照、可选择/切换 Agent
  （``GET /agents`` 下拉，P3-15 口径）、可新建 Agent。
  根选取行为（P3-15 裁决，serve 端点无状态、选取全在客户端）：
  页面加载时 ``GET /agents`` 列出根 Agent（id + 最后回复前缀 +
  最后修改时间，口径同 REPL ``/agents``）；**多根**默认选中最后
  修改时间最新的根，页面常显当前目标 id，下拉可切换；**零根**
  显示空态页并提供「新建 Agent」入口（经 ``POST /agents``——
  创建通道存在，不构成越权）。发送消息后页面立即建立/复用该
  Agent 的 SSE 订阅，渲染 delta 流至 ``turn_end``。
- 项目级自定义前端（由 Web 暴露层扩展注册/替换资产来源的开放机制）
  **初版不实现**；初版仅约定 :func:`get_frontend_assets` 的签名与
  返回结构是稳定契约，未来的扩展机制在不改变本签名的前提下接入。
- 流式推送由 serve 的 SSE 端点承载（``GET /agents/<id>/stream``，
  见 ``flowing.interfaces.serve``）——本模块前端是它的消费方；
  WebSocket 不引入。
- 本模块不做任何构建/打包：资产在 import 本模块时即已就绪，
  ``GET /`` 与 ``GET /assets/*`` 只是读取
  :class:`FrontendAssets` 的字段。

.. rubric:: 消息入口唯一性

前端页面与 Runtime 的交互**只有封闭端点集一条通道**——
``POST /agents/<agent-id>/message`` 投递消息、``GET /agents`` /
``GET /snapshot`` 读取、``POST /agents`` 创建（目标 agent-id 的选择
完全在客户端完成：serve 端点对根选取无状态，P3-15 裁决）。前端不得
绕过 serve 的 HTTP API 另开消息通道（例如直连
Agent 的内部方法）；这保证「HTTP 请求与 CLI 输入等价」的不变量在
web 形态下同样成立。

.. seealso::

    :func:`flowing.interfaces.web.cmd_web`
        本模块资产的唯一消费者（web 增量端点 ``GET /`` 与
        ``GET /assets/*``）。
    :data:`flowing.interfaces.serve.SERVE_ENDPOINTS`
        前端赖以交互的 HTTP API 封闭集。
"""

from collections.abc import Mapping

from flowing.runtime import launch

from flowing.interfaces import EXIT_OK, _install_signal_handlers


class FrontendAssets:
    """前端资产包：入口页 HTML + 静态资源表。

    .. rubric:: 功能介绍

    ``web`` 子命令挂载前端所需的全部内容的自包含结构体。
    资产在 ``flowing.interfaces.web`` 层就绪（内置默认前端随包发布），
    ``GET /`` 返回 :attr:`index_html`，``GET /assets/<name>`` 在
    :attr:`assets` 中按键查找。

    .. rubric:: 设计动机

    把「前端长什么样」收敛为一个纯数据结构，使 web 层不需要理解
    前端的任何内部组织（模板、构建管线、框架选型）；资产包是
    不可变的值对象，加载一次即可被任意多请求复用。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.interfaces.web import get_frontend_assets

        assets = get_frontend_assets()
        html = assets.index_html            # GET / 的响应体
        js = assets.assets["app.js"]        # GET /assets/app.js 的响应体

    .. rubric:: 行为规约

    实例为只读值对象：字段在构造后不被修改，调用方不得原地改
    :attr:`assets` 的内容。:attr:`index_html` 非空且是完整 HTML
    文档；:attr:`assets` 的键为 ``/assets/`` 下的相对路径（不含
    前导斜杠、不含 ``..`` 段），值为资源字节流。未命中的键由 web
    层映射为 ``404``，本类不负责错误响应。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.interfaces.web.cmd_web()``（时机：``web`` 子命令处理
      ``GET /`` 与 ``GET /assets/<name>`` 的每次请求，分别读
      :attr:`index_html` 与 :attr:`assets`）
    - 实例化方：``flowing.interfaces.web.get_frontend_assets()``（时机：每次
      调用返回资产包，允许返回同一缓存实例）

    .. seealso:: :func:`get_frontend_assets`、:func:`flowing.interfaces.web.cmd_web`
    """

    index_html: str
    """前端入口页 HTML 文档（功能 + 动机合并：``GET /`` 的唯一响应
    内容；自包含完整文档，浏览器打开即可加载 :attr:`assets` 中的
    静态资源）。
    
    行为边界：非空 ``str``；引用的静态资源路径必须以 ``/assets/``
    为前缀，且每个被引用的路径（去掉 ``/assets/`` 前缀后）必须
    能在 :attr:`assets` 中命中——不满足即视为资产包损坏。
    
    .. seealso:: :attr:`assets`
    """

    assets: Mapping[str, bytes]
    """静态资源表（功能 + 动机合并：``GET /assets/*`` 的查找来源；
    以内存表形式承载使 web 层无需访问文件系统）。
    
    行为边界：键为 ``/assets/`` 下的相对路径（不含前导斜杠、
    不含 ``..`` 段）；值为资源字节流；表在构造后不可变。键的
    查找是精确匹配，不做目录列举。
    
    .. seealso:: :attr:`index_html`
    """


def get_frontend_assets() -> FrontendAssets:
    """返回前端资产包。

    .. rubric:: 功能介绍

    ``web`` 子命令获取前端资产的唯一入口。初版返回框架随包发布的
    内置最小默认前端（一个经 ``POST /agents/<agent-id>/message``
    对话、可查看快照的静态页面）。

    .. rubric:: 设计动机

    「给不给前端、前端长什么样」是暴露层策略而非框架核心，因此
    核心只依赖这一个函数签名：项目级自定义前端的开放注册机制
    **初版不实现**，但本签名作为稳定契约保留——未来 Web 暴露层
    扩展在不改变签名的前提下替换资产来源。

    .. rubric:: 使用示例

    .. code-block:: python

        # flowing web <path> 的 web 层（概念性时序）
        assets = get_frontend_assets()
        # GET /          → assets.index_html
        # GET /assets/*  → assets.assets[name]，未命中 → 404

    .. rubric:: 行为规约

    期待行为：同步调用、无副作用、无网络活动；可重复调用，每次
    返回内容等价的资产包（允许返回同一缓存实例）。

    非行为：不做前端构建/打包/压缩（资产随包发布时就绪）；不读取
    子项目配置（CLI 不解析配置的原则同样约束本模块）；不接受任何
    参数——资产来源的定制点初版不存在。

    边缘情况：内置资产损坏（如 :attr:`FrontendAssets.index_html`
    引用了 :attr:`FrontendAssets.assets` 中不存在的键）属于发布
    缺陷，调用方按未命中键映射 ``404``，本函数不负责自检。

    .. rubric:: 测试案例

    - 前置：框架正常安装。→ 操作：调用两次。→ 期望：两次返回的
      ``index_html`` 与 ``assets`` 键集相同。
    - 前置：框架正常安装。→ 操作：解析 ``index_html`` 中全部
      ``/assets/`` 引用。→ 期望：每个引用（去前缀后）都是
      ``assets`` 的键。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.interfaces.web.FrontendAssets``（构造/返回资产包；时机：
      每次调用；docstring 规约「无副作用、无网络活动」，允许返回
      同一缓存实例）
    - 被调：``flowing.interfaces.web.cmd_web()``（时机：``web`` 子命令挂载
      ``GET /`` 与 ``GET /assets/*`` 增量端点时，前端资产的唯一来源）

    .. seealso::

        :class:`FrontendAssets`
            返回结构的字段契约。
        :func:`flowing.interfaces.web.cmd_web`
            唯一消费者；``GET /`` 与 ``GET /assets/*`` 的映射规则。
        :data:`flowing.interfaces.web.WEB_EXTRA_ENDPOINTS`
            web 相对 serve 的增量端点封闭集（本文件 :data:`WEB_EXTRA_ENDPOINTS`）。
    """
    # 内置默认前端资产随包发布即就绪：同步、无副作用、无网络活动；
    # 资产的具体装载方式规约未具名符号（不虚构加载函数），允许返回同一缓存实例
    return FrontendAssets(index_html="...", assets={})

WEB_EXTRA_ENDPOINTS: tuple[str, ...] = ("GET /", "GET /assets/*")
"""``web`` 相对 ``serve`` 的增量端点封闭集（功能 + 动机合并：web 就是
「serve + 一个策略性前端」，前端不进入框架核心；替换或移除前端
不影响 :data:`SERVE_ENDPOINTS` 中的 HTTP API）。

行为边界：

- ``GET /``          返回 ``get_frontend_assets().index_html``
（前端入口页）。
- ``GET /assets/*``  返回 ``get_frontend_assets().assets`` 中对应
键的静态资源（js/css 等）。

web **只有封闭端点集一条消息入口**——前端页面同样经
``POST /agents/<agent-id>/message`` 与 Runtime 交互，不允许绕过
serve 的 HTTP API 另开消息通道。

.. seealso:: :func:`cmd_web`、:func:`flowing.interfaces.web.get_frontend_assets`
"""

async def cmd_web(
    path: str,
    main_file: str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: str | bool,
) -> int:
    """``flowing web <path>``：serve 骨架 + serve 前端页面。

    .. rubric:: 功能介绍

    与 :func:`cmd_serve` 共享同一个 HTTP 骨架与全部
    :data:`SERVE_ENDPOINTS`，**额外**挂载 :data:`WEB_EXTRA_ENDPOINTS`
    （``GET /`` 返回前端入口页、``GET /assets/*`` 返回静态资源），
    供浏览器直接打开交互。前端资产来自 :func:`flowing.interfaces.web.get_frontend_assets`。

    .. rubric:: 设计动机

    前端是**暴露层的策略**，不是框架核心：框架只提供 HTTP API 这个
    机制（serve 已完整），「给不给前端、前端长什么样」由 Web 暴露层
    决定。``web`` 只是「serve + 加载前端资产」的便捷组合——替换或
    移除前端不影响任何 HTTP API，也只有
    ``POST /agents/<agent-id>/message`` 一条消息入口。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing web .
        # 浏览器打开 http://127.0.0.1:8000/ 即可对话

    .. rubric:: 行为规约

    期待行为：完全继承 :func:`cmd_serve` 的时序与端点契约，增量为：

    - ``GET /`` → ``200``，body 为 ``get_frontend_assets().index_html``，
      ``Content-Type: text/html``。
    - ``GET /assets/<name>`` → 在 ``get_frontend_assets().assets``
      中按键查找，命中返回 ``200`` 与对应字节流；未命中 ``404``。

    非行为：不为前端另开消息通道（前端经
    ``POST /agents/<agent-id>/message`` 与 Runtime 交互）；不在 CLI
    层做任何前端构建/打包（资产在 ``flowing.interfaces.web`` 层就绪）。

    边缘情况：``get_frontend_assets()`` 返回的资产缺失 ``/assets/*``
    中被引用的键时按未命中处理（``404``），不报错退出。

    .. rubric:: 测试案例

    - 前置：合法项目。→ 操作：``GET /``。→ 期望：``200``，HTML 中
      引用的所有 ``/assets/*`` 路径随后均可 ``GET`` 到 ``200``。
    - 前置：合法项目。→ 操作：页面脚本
      ``POST /agents/<agent-id>/message``。→ 期望：与
      :func:`cmd_serve` 的端点契约完全一致。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.interfaces.web.get_frontend_assets()``（时机：``GET /``
      与 ``GET /assets/*`` 每次请求；web.pyi 称其为本函数的唯一
      消费者）；其余调用完全继承 ``flowing.interfaces.serve.cmd_serve``
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``web`` 分发）

    .. seealso::

        :func:`cmd_serve`、:data:`WEB_EXTRA_ENDPOINTS`
        :func:`flowing.interfaces.web.get_frontend_assets` —— 前端资产的唯一来源。
    """
    # 时序与端点契约完全继承 cmd_serve（同一 HTTP 骨架）
    runtime = await launch(path, main_file=main_file, **kwargs)
    _install_signal_handlers(runtime)
    assets = get_frontend_assets()
    # GET /          每次请求 → 200 assets.index_html（Content-Type: text/html）
    # GET /assets/<name> 每次请求 → assets.assets 按键查找，命中 200 字节流、
    #   未命中 404（资产缺键按未命中处理，不报错退出）
    await runtime
    return EXIT_OK
