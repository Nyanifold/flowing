"""``flowing.interfaces.web`` —— ``web`` 子命令与内置默认前端资产。

.. rubric:: 功能介绍

本模块定义 ``web`` 子命令与 Web 暴露层的唯一交接点：
:func:`get_frontend_assets`。在 flowing 的架构里，UI 是几个「下游
应用」的例子——``flowing.interfaces.web`` 承载的只是 ``web`` 子命令
所需的**内置默认前端资产**：一个用公开 API 拼出的可对话页面（发消息、
流式显示、查快照、选择 / 切换 / 新建 Agent）。

框架核心只提供「HTTP API」这个机制（``flowing serve`` 已完整）；
「给不给前端、前端长什么样」是暴露层的策略，由本模块承载。替换或
移除前端不影响 :data:`flowing.interfaces.serve.SERVE_ENDPOINTS` 中的
任何一条。``web`` 命令只是「serve + 加载本模块前端资产」的便捷组合。

.. rubric:: 内置前端范围

内置默认前端是持续对话页面：经 ``POST /agents/<agent-id>/message``
发消息，经 ``GET /agents/<agent-id>/stream`` （SSE）流式显示生成过程
（正在生成 → 流式 delta → 完整生成两段式；工具调用与 steer 注入经
``message`` 事件可见），可查看快照，可选择 / 切换 Agent（``GET
/agents`` 下拉），可新建 Agent。

根选取行为（serve 端点对根选取无状态，选取全在客户端）：页面加载时
``GET /agents`` 列出根 Agent（id + 最后回复前缀 + 最后修改时间，口径
同 REPL ``/agents``）；多根默认选中最后修改时间最新的根，页面常显
当前目标 id，下拉可切换；零根显示空态页并提供「新建 Agent」入口
（经 ``POST /agents``）。

边界：

- 前端页面与 Runtime 的交互只有封闭端点集一条通道——
  ``POST /agents/<agent-id>/message`` 投递消息、``GET /agents`` /
  ``GET /snapshot`` 读取、``POST /agents`` 创建。前端不得绕过
  serve 的 HTTP API 另开消息通道（例如直连 Agent 的内部方法）；这
  保证「HTTP 请求与 CLI 输入等价」的不变量在 web 形态下同样成立。
- 项目级自定义前端（注册 / 替换资产来源的开放机制）当前不实现；
  :func:`get_frontend_assets` 的签名与返回结构是稳定契约，未来的
  扩展机制在不改变签名的前提下接入。
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
from types import MappingProxyType

from aiohttp import web
from flowing.runtime import launch

from flowing.interfaces import EXIT_OK, EXIT_RUNTIME_ERROR, _install_signal_handlers
from flowing.interfaces.serve import _build_app, _serve_runtime


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
# 内置默认前端资产本体（模块内常量：随包发布即就绪、零构建零打包）
# ---------------------------------------------------------------------------

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flowing</title>
<link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<header>
  <span class="brand">Flowing</span>
  <select id="agent-select" title="切换 Agent"></select>
  <span id="current-id" class="mono"></span>
  <button id="btn-new">新建 Agent</button>
  <button id="btn-snapshot">快照</button>
</header>
<main id="conversation"></main>
<div id="empty-state" hidden>
  <p>暂无 Agent 记录。</p>
  <button id="btn-new-empty">新建 Agent</button>
</div>
<pre id="snapshot-view" hidden></pre>
<footer>
  <input id="input" type="text" placeholder="输入消息，回车发送" autofocus>
  <button id="btn-send">发送</button>
</footer>
<script src="/assets/app.js"></script>
</body>
</html>
"""

_APP_JS = """\
"use strict";
// Flowing 内置默认前端：对话页（serve 封闭端点集的唯一消费方）。
// 通道：POST /agents/<id>/message 投递、GET /agents/<id>/stream（SSE）
// 流式显示、GET /agents 列举/切换、POST /agents 新建、GET /snapshot 查看。
const $ = (sel) => document.querySelector(sel);
const state = { current: null, es: null, generating: null };

function bubble(kind, text) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  div.textContent = text;
  $("#conversation").appendChild(div);
  div.scrollIntoView();
  return div;
}

function closeStream() {
  if (state.es) { state.es.close(); state.es = null; }
}

function openStream(agentId) {
  // 建立/复用该 Agent 的 SSE 订阅（两段式：delta 流 → turn_end 收尾）
  closeStream();
  const es = new EventSource(`/agents/${encodeURIComponent(agentId)}/stream`);
  es.addEventListener("delta", (e) => {
    if (!state.generating) state.generating = bubble("provider", "");
    state.generating.textContent += JSON.parse(e.data);
  });
  es.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    // 工具结果与 steer 注入渲染为 meta 摘要行（MessagePriority.STEER = 1）
    if (msg.kind === "tool" || msg.priority === 1) {
      const text = (msg.content || []).map((b) => b.text || "").join("");
      bubble("meta", `[${msg.kind}] ${text.slice(0, 80)}`);
    }
  });
  es.addEventListener("turn_end", () => { state.generating = null; });
  state.es = es;
}

async function loadHistory(agentId) {
  $("#conversation").innerHTML = "";
  const resp = await fetch(`/agents/${encodeURIComponent(agentId)}/messages`);
  if (!resp.ok) return;
  for (const msg of await resp.json()) {
    const text = (msg.content || []).map((b) => b.text || "").join("");
    if (msg.kind === "user" || msg.kind === "provider") bubble(msg.kind, text);
  }
}

function selectAgent(agentId) {
  state.current = agentId;
  $("#current-id").textContent = agentId || "";
  $("#empty-state").hidden = true;
  openStream(agentId);
  loadHistory(agentId);
}

async function refreshAgents() {
  const resp = await fetch("/agents");
  const agents = await resp.json();
  const select = $("#agent-select");
  select.innerHTML = "";
  for (const a of agents) {
    const opt = document.createElement("option");
    opt.value = a.agent_id;
    opt.textContent = `${a.agent_id}  "${a.last_reply || ""}"`;
    select.appendChild(opt);
  }
  if (agents.length === 0) {
    // 零根空态：显示新建入口
    closeStream();
    state.current = null;
    $("#current-id").textContent = "";
    $("#empty-state").hidden = false;
    return;
  }
  // 多根默认选中最后修改时间最新的根；已有选中且仍在名录则保持
  const keep = agents.some((a) => a.agent_id === state.current);
  const chosen = keep ? state.current
    : agents.slice().sort((a, b) =>
        (b.modified_at || "").localeCompare(a.modified_at || ""))[0].agent_id;
  select.value = chosen;
  selectAgent(chosen);
}

async function createAgent() {
  const resp = await fetch("/agents", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!resp.ok) { bubble("meta", `创建失败：${resp.status}`); return; }
  const { agent_id } = await resp.json();
  await refreshAgents();
  selectAgent(agent_id);
  $("#agent-select").value = agent_id;
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text || !state.current) return;
  input.value = "";
  bubble("user", text);
  if (!state.es) openStream(state.current);   // 发送后立即建立/复用 SSE 订阅
  state.generating = bubble("provider", "正在生成…");
  const resp = await fetch(`/agents/${encodeURIComponent(state.current)}/message`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const body = await resp.json();
  // 完整生成段：POST 响应的最终文本覆盖流式气泡（无流式时直接呈现）
  if (state.generating) {
    state.generating.textContent = body.final_text || "";
    state.generating = null;
  } else {
    bubble("provider", body.final_text || "");
  }
}

async function showSnapshot() {
  const view = $("#snapshot-view");
  if (!view.hidden) { view.hidden = true; return; }
  const resp = await fetch("/snapshot");
  view.textContent = JSON.stringify(await resp.json(), null, 2);
  view.hidden = false;
}

$("#agent-select").addEventListener("change", (e) => selectAgent(e.target.value));
$("#btn-new").addEventListener("click", createAgent);
$("#btn-new-empty").addEventListener("click", createAgent);
$("#btn-snapshot").addEventListener("click", showSnapshot);
$("#btn-send").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
refreshAgents();
"""

_STYLE_CSS = """\
body { font-family: system-ui, sans-serif; margin: 0; display: flex;
       flex-direction: column; height: 100vh; }
header { display: flex; gap: .5rem; align-items: center; padding: .5rem 1rem;
         border-bottom: 1px solid #ddd; }
.brand { font-weight: 600; }
.mono { font-family: ui-monospace, monospace; color: #666; font-size: .85em; }
main { flex: 1; overflow-y: auto; padding: 1rem; }
.msg { max-width: 70%; margin: .4rem 0; padding: .5rem .8rem;
       border-radius: .6rem; white-space: pre-wrap; }
.msg.user { margin-left: auto; background: #d7ebff; }
.msg.provider { margin-right: auto; background: #f0f0f0; }
.msg.meta { margin: .2rem auto; background: none; color: #888;
            font-size: .8em; padding: 0; }
footer { display: flex; gap: .5rem; padding: .5rem 1rem;
         border-top: 1px solid #ddd; }
footer input { flex: 1; padding: .5rem; }
#empty-state { text-align: center; margin-top: 4rem; color: #666; }
#snapshot-view { overflow: auto; max-height: 50vh; margin: 0;
                 padding: 1rem; background: #fafafa; border-top: 1px solid #ddd; }
"""

_ASSETS_CACHE: FrontendAssets | None = None


def get_frontend_assets() -> FrontendAssets:
    """返回前端资产包。

    ``web`` 子命令获取前端资产的唯一入口。返回框架随包发布的内置
    最小默认前端（一个经 ``POST /agents/<agent-id>/message`` 对话、
    可查看快照、可选择 / 切换 / 新建 Agent 的静态页面）。

    项目级自定义前端的开放注册机制当前不实现，但本签名与返回结构
    作为稳定契约保留——未来的 Web 暴露层扩展在不改变签名的前提下
    替换资产来源。

    .. rubric:: 使用示例

    .. code-block:: python

        assets = get_frontend_assets()
        # GET /          -> assets.index_html
        # GET /assets/*  -> assets.assets[name]，未命中 -> 404

    .. rubric:: 行为要点

    - 同步调用、无副作用、无网络活动。
    - 可重复调用，每次返回内容等价的资产包（允许返回同一缓存实例）。
    - 不接受任何参数；不读取子项目配置。
    - 内置资产损坏（如 :attr:`FrontendAssets.index_html` 引用了
      :attr:`FrontendAssets.assets` 中不存在的键）属于发布缺陷，
      调用方按未命中键映射 ``404``，本函数不负责自检。

    .. seealso::

        :class:`FrontendAssets`
            返回结构的字段契约。
        :func:`flowing.interfaces.web.cmd_web`
            唯一消费者；``GET /`` 与 ``GET /assets/*`` 的映射规则。
        :data:`WEB_EXTRA_ENDPOINTS`
            web 相对 serve 的增量端点封闭集。
    """
    # 内置资产为模块内常量：import 即就绪、同步无副作用；资产表经
    # MappingProxyType 包装保证只读值对象语义；首次调用构造后缓存复用
    global _ASSETS_CACHE
    if _ASSETS_CACHE is None:
        _ASSETS_CACHE = FrontendAssets(
            index_html=_INDEX_HTML,
            assets=MappingProxyType({
                "app.js": _APP_JS.encode("utf-8"),
                "style.css": _STYLE_CSS.encode("utf-8"),
            }),
        )
    return _ASSETS_CACHE


WEB_EXTRA_ENDPOINTS: tuple[str, ...] = ("GET /", "GET /assets/*")
"""``web`` 相对 ``serve`` 的增量端点封闭集：``web`` 就是「serve + 一个
策略性前端」，前端不进入框架核心——替换或移除前端不影响
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

    ``web`` 只是「serve + 加载前端资产」的便捷组合——替换或移除
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
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-m`` 传入）。
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
        print(f"launch 阶段失败：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)
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
