"""``flowing.interfaces.web`` —— ``web`` 子命令与内置默认前端资产。

.. rubric:: 功能介绍

本模块定义 ``web`` 子命令与 Web 暴露层的唯一交接点：
:func:`get_frontend_assets`。在 flowing 的架构里，UI 是几个“下游
应用”的例子——``flowing.interfaces.web`` 承载的只是 ``web`` 子命令
所需的（内置默认）前端资产：一个用公开 API 拼出的可对话页面（发消息、
流式显示、查快照、选择 / 切换 / 新建 Agent）。

框架核心只提供“HTTP API”这个机制（``flowing serve`` 已完整）；
“给不给前端、前端长什么样”是暴露层的策略，由本模块承载。替换或
移除前端不影响 :data:`flowing.interfaces.serve.SERVE_ENDPOINTS` 中的
任何一条。``web`` 命令只是“serve + 加载本模块前端资产”的便捷组合。

.. rubric:: 内置前端范围

内置默认前端是持续对话页面：经 ``POST /agents/<agent-id>/message``
发消息，经 ``GET /agents/<agent-id>/stream`` （SSE）流式显示生成过程
（正在生成 → 流式 delta → 完整生成两段式；工具调用与 steer 注入经
``message`` 事件可见），可查看快照，可选择 / 切换 Agent（``GET
/agents`` 下拉），可新建 Agent。

根选取行为（serve 端点对根选取无状态，选取全在客户端）：页面加载时
``GET /agents`` 列出根 Agent（id + 最后回复前缀 + 最后修改时间，口径
同 REPL ``/agents``）；多根默认选中最后修改时间最新的根，页面常显
当前目标 id，下拉可切换；零根显示空态页并提供“新建 Agent”入口
（经 ``POST /agents``）。

边界：

- 前端页面与 Runtime 的交互只有 serve 的封闭 HTTP 端点一条通道
  （消息投递 / 观察流 / 控制与读取端点）；前端不绕过 serve API 另开
  消息通道（例如直连 Agent 的内部方法）。这保证“HTTP 请求与 CLI
  输入等价”的不变量在 web 形态下同样成立。
- 本模块不做任何构建 / 打包：资产在 import 本模块时即已就绪，
  ``GET /`` 与 ``GET /assets/*`` 只是读取 :class:`FrontendAssets`
  的字段。WebSocket 不引入——流式推送由 serve 的 SSE 端点承载。

.. rubric:: 使用示例

.. code-block:: console

    $ flowing web .
    # 浏览器打开 http://127.0.0.1:8000/ 即可对话

.. seealso::

    :func:`flowing.interfaces.web.cmd_web`
        本模块资产的唯一消费者（web 增量端点 ``GET /`` 与
        ``GET /assets/*``）。
    :data:`flowing.interfaces.serve.SERVE_ENDPOINTS`
        前端赖以交互的 HTTP API 封闭集。
"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from aiohttp import web
from flowing.runtime import launch

from flowing.interfaces import EXIT_OK, EXIT_RUNTIME_ERROR
from flowing.interfaces.serve import _build_app, _serve_runtime


def _install_signal_handlers(runtime) -> None:
    """安装 SIGINT / SIGTERM → ``runtime.shutdown()`` 的优雅关闭桥
    （web 子命令本地件，不属稳定契约）。非主线程 / 不支持信号的
    平台为 no-op。"""
    import asyncio
    import signal

    def _handler(signum: int, frame: object) -> None:
        # 信号处理器跑在主线程、事件循环阻塞在 select 时 ensure_future 不会
        # 唤醒循环（PEP 475 自动重试 select）→ call_soon_threadsafe 经
        # self-pipe 显式唤醒
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(asyncio.ensure_future, runtime.shutdown())

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError, RuntimeError):
        pass


@dataclass(frozen=True)
class FrontendAssets:
    """前端资产包：入口页 HTML + 静态资源表。

    ``web`` 子命令挂载前端所需的全部内容的自包含结构体，实例为只读
    值对象（字段构造后不被修改）。``GET /`` 返回 :attr:`index_html`，
    ``GET /assets/<name>`` 在 :attr:`assets` 中按键查找。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.interfaces.web import get_frontend_assets

        assets = get_frontend_assets()
        html = assets.index_html            # GET / 的响应体
        js = assets.assets["app.js"]        # GET /assets/app.js 的响应体

    .. rubric:: 行为要点

    - :attr:`index_html` 非空且是完整 HTML 文档。
    - :attr:`assets` 的键为 ``/assets/`` 下的相对路径（不含前导斜杠、
      不含 ``..`` 段），值为资源字节流。
    - 未命中的键由 web 层映射为 ``404``，本类不负责错误响应。

    .. seealso:: :func:`get_frontend_assets`、:func:`flowing.interfaces.web.cmd_web`
    """

    index_html: str
    """前端入口页 HTML 文档：``GET /`` 的唯一响应内容；自包含完整
    文档，浏览器打开即可加载 :attr:`assets` 中的静态资源。

    行为边界：非空 ``str``；引用的静态资源路径必须以 ``/assets/``
    为前缀，且每个被引用的路径（去掉 ``/assets/`` 前缀后）必须能在
    :attr:`assets` 中命中——不满足即视为资产包损坏。

    .. seealso:: :attr:`assets`
    """

    assets: Mapping[str, bytes]
    """静态资源表：``GET /assets/*`` 的查找来源；以内存表形式承载，
    web 层无需访问文件系统。

    行为边界：键为 ``/assets/`` 下的相对路径（不含前导斜杠、不含
    ``..`` 段）；值为资源字节流；表在构造后不可变。键的查找是精确
    匹配，不做目录列举。

    .. seealso:: :attr:`index_html`
    """


# ---------------------------------------------------------------------------
# 内置默认前端资产：webui 构建产物（前端源工程在仓库根 webui/——源工程
# 不入包（wheel 只发产物）；React 19 +
# TS + Vite + Tailwind 4 + vite-plugin-singlefile——技术栈复用 kimi-code 的
# @moonshot-ai/vis-web 构建组合，kimi-code @ a4a7df2 2026-09-10；样式变量
# 直接借用 dist-web 的亮色 :root 主题）。构建产物是单文件
# webui-dist/index.html（JS/CSS 全内联），随包发布。
# ---------------------------------------------------------------------------

_WEBUI_DIST = Path(__file__).parent / "webui-dist" / "index.html"

_ASSETS_CACHE: FrontendAssets | None = None


def get_frontend_assets() -> FrontendAssets:
    """返回前端资产包。

    ``web`` 子命令获取前端资产的唯一入口。资产本体是
    仓库根 ``webui/`` 的构建产物（单文件
    ``webui-dist/index.html``，全部 JS/CSS 内联）：亮色聊天界面（样式变量
    借用 kimi-code web 界面亮色主题）：agent 侧栏（含最近回复预览）+
    “记录 / 树”双子页（记录视图渲染消息树——仅分叉处缩进、分叉首条
    消息可开合、每条消息带预览，树子页可点节点切换分支），消息正文
    Markdown 渲染（react-markdown + GFM）、思考折叠、工具调用/返回折叠卡
    （展开为 JSON 高亮）、输入框上方上下文占用进度条、头部模型选择；
    输入框支持消息与 repl 指令（经 serve 的观察 / 控制端点）。

    本内置资产即 ``web`` 的默认前端来源（``GET /`` 与 ``GET /assets/*``
    的返回即此资产包；单文件形态下 ``assets`` 表为空，``/assets/*``
    一律 404）。

    .. rubric:: 使用示例

    .. code-block:: python

        assets = get_frontend_assets()
        # GET /          -> assets.index_html
        # GET /assets/*  -> assets.assets[name]，未命中 -> 404

    .. rubric:: 行为要点

    - 同步调用、只读发布目录文件、无网络活动。
    - 可重复调用，每次返回内容等价的资产包（允许返回同一缓存实例）。
    - 不接受任何参数；不读取子项目配置。
    - ``webui-dist/index.html`` 缺失（仓库未构建即打包）→
      :class:`FileNotFoundError`（发布缺陷，fail fast 不静默降级）。

    .. seealso::

        :class:`FrontendAssets`
            返回结构的字段契约。
        :func:`flowing.interfaces.web.cmd_web`
            唯一消费者；``GET /`` 与 ``GET /assets/*`` 的映射规则。
        :data:`WEB_EXTRA_ENDPOINTS`
            web 相对 serve 的增量端点封闭集。
    """
    # 首次调用读盘后缓存复用；缺失即 FileExistsError 族 fail fast
    global _ASSETS_CACHE
    if _ASSETS_CACHE is None:
        _ASSETS_CACHE = FrontendAssets(
            index_html=_WEBUI_DIST.read_text(encoding="utf-8"),
            assets=MappingProxyType({}),
        )
    return _ASSETS_CACHE


WEB_EXTRA_ENDPOINTS: tuple[str, ...] = ("GET /", "GET /assets/*")
"""``web`` 相对 ``serve`` 的增量端点封闭集：``web`` 就是“serve + 一个
策略性前端”，前端不进入框架核心——替换或移除前端不影响
:data:`SERVE_ENDPOINTS` 中的任何 HTTP API。

- ``GET /``：返回 ``get_frontend_assets().index_html`` （前端入口页）。
- ``GET /assets/*``：返回 ``get_frontend_assets().assets`` 中对应
  键的静态资源（js / css 等）。

``web`` 只有封闭端点集一条消息入口——前端页面同样经
``POST /agents/<agent-id>/message`` 与 Runtime 交互，不允许绕过
serve 的 HTTP API 另开消息通道。

.. seealso:: :func:`flowing.interfaces.web.cmd_web`、:func:`flowing.interfaces.web.get_frontend_assets`
"""


def _asset_content_type(name: str) -> str:
    """静态资源的 Content-Type 推断（按键名后缀；未知后缀回退二进制流）。"""
    if name.endswith(".js"):
        return "text/javascript"
    if name.endswith(".css"):
        return "text/css"
    return "application/octet-stream"


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

    与 :func:`flowing.interfaces.serve.cmd_serve` 共享同一个 HTTP
    骨架与全部 :data:`SERVE_ENDPOINTS`，额外挂载
    :data:`WEB_EXTRA_ENDPOINTS` （``GET /`` 返回前端入口页、
    ``GET /assets/*`` 返回静态资源），供浏览器直接打开交互。前端
    资产来自 :func:`flowing.interfaces.web.get_frontend_assets`。

    ``web`` 只是“serve + 加载前端资产”的便捷组合——替换或移除
    前端不影响任何 HTTP API，前端也只有
    ``POST /agents/<agent-id>/message`` 一条消息入口。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing web .
        # 浏览器打开 http://127.0.0.1:8000/ 即可对话

    .. rubric:: 行为要点

    时序与端点契约完全继承 :func:`flowing.interfaces.serve.cmd_serve`
    （同一 HTTP 骨架），增量为：

    - ``GET /`` → ``200``，body 为 ``get_frontend_assets().index_html``，
      ``Content-Type: text/html``。
    - ``GET /assets/<name>`` → 在 ``get_frontend_assets().assets``
      中按键精确查找，命中返回 ``200`` 与对应字节流；未命中
      ``404`` （含资产缺键：按未命中处理，不报错退出）。

    边界：不为前端另开消息通道（前端经 ``POST /agents/<agent-id>/message``
    与 Runtime 交互）；不在 CLI 层做任何前端构建 / 打包（资产在
    ``flowing.interfaces.web`` 层就绪）。

    :param path: 子项目路径（普通文件系统路径）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-f`` 传入）。
    :param host: 监听地址，默认 ``127.0.0.1``。
    :param port: 监听端口，默认 ``8000``。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK` （正常关闭）或
        :data:`EXIT_RUNTIME_ERROR` （``launch`` 失败或端口绑定失败）。

    .. seealso::

        :func:`flowing.interfaces.serve.cmd_serve`、:data:`WEB_EXTRA_ENDPOINTS`
        :func:`flowing.interfaces.web.get_frontend_assets` —— 前端资产的唯一来源。
    """
    # 时序与端点契约完全继承 cmd_serve（同一 HTTP 骨架）
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)
    # 访问日志与框架 warning 上屏（aiohttp.access 走 stdlib logging）
    import logging
    logging.basicConfig(level=logging.INFO)
    assets = get_frontend_assets()

    async def _index(request: web.Request) -> web.Response:
        return web.Response(text=assets.index_html, content_type="text/html")

    async def _asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        blob = assets.assets.get(name)   # 按键精确匹配，不做目录列举
        if blob is None:
            return web.Response(status=404)   # 未命中（含资产缺键）：404 不报错退出
        return web.Response(body=blob, content_type=_asset_content_type(name))

    extra_routes = (
        ("GET", "/", _index),
        ("GET", "/assets/{name}", _asset),
    )
    # 增量注册表与 WEB_EXTRA_ENDPOINTS 一一对应（封闭集断言）
    assert tuple(f"{m} {p.replace('/{name}', '/*')}" for m, p, _ in extra_routes) == WEB_EXTRA_ENDPOINTS
    return await _serve_runtime(runtime, _build_app(runtime, extra_routes), host, port)
