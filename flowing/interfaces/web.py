"""``flowing.interfaces.web`` —— ``web`` 子命令与内置默认前端资产。

.. rubric:: 功能介绍

本模块定义 ``web`` 子命令与 Web 暴露层的唯一交接点：
:func:`get_frontend_assets`。在 flowing 的架构里，UI 是几个「下游
应用」的例子——``flowing.interfaces.web`` 承载的只是 ``web`` 子命令
所需的（内置默认）前端资产：一个用公开 API 拼出的可对话页面（发消息、
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

- 前端页面与 Runtime 的交互只有 serve 的封闭 HTTP 端点一条通道
  （消息投递 / 观察流 / 控制与读取端点）；前端不绕过 serve API 另开
  消息通道（例如直连 Agent 的内部方法）。这保证「HTTP 请求与 CLI
  输入等价」的不变量在 web 形态下同样成立。
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

_INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flowing</title>
<link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<div id="layout">
  <aside id="sidebar">
    <div class="side-head"><span class="brand">Flowing</span>
      <button id="btn-new" title="新建 Agent">+</button></div>
    <div id="agent-tree" class="tree"></div>
  </aside>
  <main>
    <header>
      <span id="view-tabs">
        <button class="tab active" data-view="record">记录</button>
        <button class="tab" data-view="tree">树</button>
      </span>
      <span id="current-id" class="mono"></span>
      <span id="busy" hidden>…生成中…</span>
    </header>
    <div id="record-view">
      <div id="conversation"></div>
      <div id="empty-state" hidden><p>选择左侧一个 Agent，或新建。</p></div>
    </div>
    <div id="tree-view" hidden></div>
    <pre id="meta" hidden></pre>
    <footer>
      <input id="input" type="text" autofocus autocomplete="off"
             placeholder="输入消息；以 / 开头使用 repl 指令" spellcheck="false">
      <button id="btn-send">发送</button>
    </footer>
  </main>
</div>
<script src="/assets/app.js"></script>
</body>
</html>
"""

_APP_JS = r"""
"use strict";
// Flowing 内置前端 v3.1：agent 继承树侧栏 + 「记录(从 head 上溯)/树(切分支)」
// 双子页 + 可输入 repl 指令的输入框。通道均为 serve HTTP API：
//  GET /agents（含 parent_agent_id / name）、POST /agents（id-adopt）、
//  GET /agents/<id>/messages、GET /agents/<id>/tree、POST …/rewind、
//  POST …/message、POST …/cancel、GET …/stream(SSE: message/thinking/text/turn_end)。
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const state = {
  agents: [], current: null, es: null, view: "record",
  rows: new Map(), generating: null, running: false,
};
const storeKey = "flowing.web.agent";

function rowKeyOf(id) { return id; }

function appendText(el, text) {
  const span = document.createElement("span");
  span.textContent = text;
  el.appendChild(span);
  el.scrollTop = el.scrollHeight;
}

function bubble(el, cls, text) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.textContent = text;
  el.appendChild(d);
  el.scrollTop = el.scrollHeight;
  return d;
}

function setBusy(on) { state.running = on; $("#busy").hidden = !on; }

// ---- 侧边栏：agent 继承树 --------------------------------------------------
function renderTree() {
  const root = $("#agent-tree");
  root.innerHTML = "";
  const children = new Map();   // parent_id -> [agent]
  for (const a of state.agents) {
    const p = a.parent_agent_id || null;
    if (!children.has(p)) children.set(p, []);
    children.get(p).push(a);
  }
  function build(parentId) {
    const items = (children.get(parentId) || []).slice()
      .sort((a, b) => (a.name || a.agent_id).localeCompare(b.name || b.agent_id));
    if (items.length === 0) return null;
    const ul = document.createElement("ul");
    for (const a of items) {
      const li = document.createElement("li");
      li.className = a.agent_id === state.current ? "current" : "";
      const row = document.createElement("div");
      row.className = "agent";
      const kids = build(a.agent_id);
      if (kids) {
        const toggle = document.createElement("span");
        toggle.className = "toggle";
        toggle.textContent = "▾";
        toggle.onclick = (e) => {
          e.stopPropagation();
          kids.hidden = !kids.hidden;
          toggle.textContent = kids.hidden ? "▸" : "▾";
        };
        row.appendChild(toggle);
      } else {
        row.appendChild(document.createElement("span"));
      }
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = a.name || a.agent_id;
      label.title = `${a.agent_id}  ${a.last_reply || ""}`;
      row.appendChild(label);
      row.onclick = () => focus(a.agent_id);
      li.appendChild(row);
      if (kids) li.appendChild(kids);
      ul.appendChild(li);
    }
    return ul;
  }
  const top = build(null);
  if (top) root.appendChild(top);
  else bubble(root, "meta", "(no agents)");
}

async function refreshAgents() {
  const resp = await fetch("/agents");
  if (!resp.ok) return;
  state.agents = await resp.json();
  renderTree();
  if (!state.current && state.agents.length) focus(state.agents[0].agent_id);
}

// ---- 当前 agent 视角 --------------------------------------------------------
async function focus(id) {
  state.current = id;
  localStorage.setItem(storeKey, id);
  $("#current-id").textContent = id;
  $("#empty-state").hidden = true;
  renderTree();
  openStream(id);
  await showView("record");
}

function closeStream() { if (state.es) { state.es.close(); state.es = null; } state.generating = null; }

function openStream(id) {
  closeStream();
  const es = new EventSource(`/agents/${encodeURIComponent(id)}/stream`);
  const conv = $("#conversation");
  es.addEventListener("thinking", (e) => {
    const d = JSON.parse(e.data);
    const key = rowKeyOf(d.message_id || "g" + state.generatingIndex());
    let el = state.rows.get(key);
    if (!el) { el = bubble(conv, "thinking", ""); state.rows.set(key, el); }
    appendText(el, d.text || "");
    setBusy(true);
  });
  es.addEventListener("delta", (e) => {
    const d = JSON.parse(e.data);
    const key = rowKeyOf(d.message_id || "g" + state.generatingIndex());
    let el = state.rows.get(key);
    if (!el || el.dataset.role !== "provider") {
      el = bubble(conv, "provider", "");
      el.dataset.role = "provider";
      state.rows.set(key, el);
    }
    appendText(el, d.text || "");
    state.generating = el;
    setBusy(true);
  });
  es.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    setBusy(false);
    // 只在记录视图把新消息落行；user/provider/tool 各按其类
    if (m.kind === "tool") {
      const text = (m.content || []).map((b) => b.text || b.data ? JSON.stringify(b.data) : "").join("");
      bubble(conv, "meta", `[tool:${m.tool_status || ""}] ${esc(text).slice(0, 120)}`);
      return;
    }
    const key = rowKeyOf(m.id);
    let el = state.rows.get(key);
    if (el) return;   // 已在流式增量中
    const cls = m.kind === "user" ? "user" : "provider";
    el = bubble(conv, cls, "");
    el.dataset.role = m.kind === "user" ? "user" : "provider";
    state.rows.set(key, el);
    const text = (m.content || []).map((b) => b.text || "").join("");
    appendText(el, text);
  });
  es.addEventListener("turn_end", (e) => { setBusy(false); state.generating = null; });
  es.onerror = () => { es.close(); setBusy(false); };
  state.es = es;
}
state.generatingIndex = () => (state._g = (state._g || 0) + 1);

async function loadRecord(id) {
  const conv = $("#conversation");
  conv.innerHTML = "";
  state.rows.clear();
  const resp = await fetch(`/agents/${encodeURIComponent(id)}/messages`);
  if (!resp.ok) return;
  const msgs = await resp.json();
  for (const m of msgs) {
    const text = (m.content || []).map((b) => b.text || "").join("");
    const think = (m.content || []).map((b) => b.thinking || "").join("");
    if (m.kind === "user") { const el = bubble(conv, "user", text); state.rows.set(rowKeyOf(m.id), el); }
    else if (m.kind === "provider") {
      if (think) { const t = bubble(conv, "thinking", think); t.onclick = () => t.classList.toggle("folded"); t.classList.add("folded"); }
      const el = bubble(conv, "provider", text); state.rows.set(rowKeyOf(m.id), el);
    }
  }
}

// ---- 「树」子页：消息树 + 切换分支 ------------------------------------------
async function loadTree(id) {
  const host = $("#tree-view");
  host.innerHTML = "";
  const resp = await fetch(`/agents/${encodeURIComponent(id)}/tree`);
  if (!resp.ok) { bubble(host, "meta", "tree unavailable"); return; }
  const t = await resp.json();
  const hdr = document.createElement("div");
  hdr.className = "meta";
  hdr.textContent = `head=${t.head}`;
  host.appendChild(hdr);
  function nodeEl(n) {
    const li = document.createElement("li");
    li.className = n.head ? "head" : "";
    const d = document.createElement("div");
    d.className = "node";
    d.textContent = `${n.kind}:${n.id.slice(0, 12)}${n.head ? " ←head" : ""}`;
    d.title = n.id;
    d.onclick = async () => {
      await fetch(`/agents/${encodeURIComponent(id)}/rewind`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message_id: n.id }) });
      await showView("record");
    };
    li.appendChild(d);
    if (n.children && n.children.length) {
      const ul = document.createElement("ul");
      for (const c of n.children) ul.appendChild(nodeEl(c));
      li.appendChild(ul);
    }
    return li;
  }
  const ul = document.createElement("ul");
  for (const r of t.roots) ul.appendChild(nodeEl(r));
  host.appendChild(ul);
}

async function showView(v) {
  state.view = v;
  for (const b of document.querySelectorAll("#view-tabs .tab"))
    b.classList.toggle("active", b.dataset.view === v);
  $("#record-view").hidden = v !== "record";
  $("#tree-view").hidden = v !== "tree";
  if (v === "record" && state.current) await loadRecord(state.current);
  else if (v === "tree" && state.current) await loadTree(state.current);
}

// ---- 输入框：普通消息 vs repl 指令 -------------------------------------------
async function runCommand(text) {
  const meta = $("#meta");
  meta.hidden = false;
  meta.textContent = "";
  bubble(meta, "meta", "» " + text);
  const parts = text.slice(1).split(/\s+/);
  const cmd = parts[0];
  const arg = parts.slice(1).join(" ").trim();
  const out = [];
  try {
    if (cmd === "new") {   // 前端侧：新建并切换聚焦（repl /new 语义）
      const r = await fetch("/agents", { method: "POST", body: "{}",
        headers: { "Content-Type": "application/json" } });
      const b = await r.json(); await refreshAgents(); await focus(b.agent_id);
      out.push("bound " + b.agent_id);
    } else if (cmd === "agent") {   // 前端侧：切换聚焦
      const hit = state.agents.find((a) => a.agent_id === arg || a.name === arg);
      if (hit) { await focus(hit.agent_id); out.push("focus " + hit.agent_id); }
      else out.push("unknown agent: " + arg);
    } else if (state.current) {   // 其余命令走共享命令目录（serve POST …/command）
      const r = await fetch(`/agents/${encodeURIComponent(state.current)}/command`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line: text }) });
      const b = await r.json();
      out.push(...(b.lines || []));
    } else {
      out.push("no agent focused (click one in the sidebar)");
    }
  } catch (err) { out.push("error: " + err.message); }
  out.forEach((l) => bubble(meta, "meta", l));
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (text.startsWith("/")) { await runCommand(text); return; }
  if (!state.current) return;
  bubble($("#conversation"), "user", text);
  setBusy(true);
  const resp = await fetch(`/agents/${encodeURIComponent(state.current)}/message`,
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
  setBusy(false);
  if (!resp.ok) return;
  const body = await resp.json();
  const conv = $("#conversation");
  const key = rowKeyOf(body.message_id);   // 挂树 USER 消息
  if (state.generating) {
    state.generating.textContent = body.final_text || "";
    state.generating = null;
  } else {
    const el = bubble(conv, "provider", body.final_text || "");
    state.rows.set(key + ":reply", el);
  }
}

$("#btn-send").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$("#btn-new").addEventListener("click", async () => {
  const r = await fetch("/agents", { method: "POST", body: "{}",
    headers: { "Content-Type": "application/json" } });
  const b = await r.json(); await refreshAgents(); await focus(b.agent_id);
});
for (const b of document.querySelectorAll("#view-tabs .tab"))
  b.addEventListener("click", () => showView(b.dataset.view));

const saved = localStorage.getItem(storeKey);
refreshAgents().then(() => {
  if (saved && state.agents.some((a) => a.agent_id === saved)) focus(saved);
});
"""

_STYLE_CSS = r"""
body { margin: 0; font-family: system-ui, sans-serif; height: 100vh; display: flex; }
#layout { display: flex; width: 100%; height: 100%; }
#sidebar { width: 260px; border-right: 1px solid #ddd; overflow: auto; padding: .5rem; }
.side-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: .5rem; }
.brand { font-weight: 600; }
#agent-tree ul { list-style: none; padding-left: 1em; margin: 0; }
#agent-tree .agent { cursor: pointer; padding: .15em .2em; border-radius: 4px; }
#agent-tree .agent:hover { background: #eef2ff; }
#agent-tree .current .label { font-weight: 700; }
.toggle { display: inline-block; width: 1em; cursor: pointer; color: #888; }
main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
header { display: flex; gap: .8rem; align-items: center; padding: .5rem 1rem;
         border-bottom: 1px solid #ddd; }
#view-tabs .tab { border: 1px solid #bbb; background: #fff; padding: .2em .6em;
                  border-radius: 6px; cursor: pointer; }
#view-tabs .tab.active { background: #2563eb; color: #fff; border-color: #2563eb; }
.mono { font-family: ui-monospace, monospace; font-size: .8em; color: #666;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#record-view { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
#conversation { flex: 1; padding: 1rem; }
.msg { max-width: 78%; margin: .4rem 0; padding: .5rem .8rem; border-radius: .6rem;
       white-space: pre-wrap; }
.msg.user { margin-left: auto; background: #d7ebff; }
.msg.provider { margin-right: auto; background: #f0f0f0; }
.msg.thinking { margin-right: auto; background: #faf5d0; color: #6b5b00; font-size: .85em;
                border-left: 3px solid #d9c400; cursor: pointer; }
.msg.thinking.folded { max-height: 1.6em; overflow: hidden; }
.msg.meta { margin: .2rem auto; background: none; color: #888; font-size: .8em; padding: 0; white-space: normal; }
#tree-view { flex: 1; overflow: auto; padding: 1rem; font-family: ui-monospace, monospace; font-size: .85em; }
#tree-view ul { list-style: none; padding-left: 1.2em; }
#tree-view .node { cursor: pointer; padding: 1px 2px; }
#tree-view .node:hover { background: #eef2ff; }
#tree-view .head { color: #2563eb; font-weight: 700; }
#meta { flex: 0 1 auto; max-height: 30%; overflow: auto; padding: .5rem 1rem;
        background: #fafafa; border-top: 1px solid #eee; }
footer { display: flex; gap: .5rem; padding: .5rem 1rem; border-top: 1px solid #ddd; }
footer input { flex: 1; padding: .5rem; }
#empty-state { text-align: center; margin-top: 4rem; color: #666; }

"""
_ASSETS_CACHE: FrontendAssets | None = None


def get_frontend_assets() -> FrontendAssets:
    """返回前端资产包。

    ``web`` 子命令获取前端资产的唯一入口。返回框架随包发布的内置
    默认前端：agent 继承树侧栏 + 「记录 / 树」双子页（记录从当前
    head 上溯，树可点节点切换分支），输入框支持消息与 repl 指令
    （经 serve 的观察 / 控制端点，含思考折叠展示）。

    本内置资产即 ``web`` 的默认前端来源（``GET /`` 与 ``GET /assets/*``
    的返回即此资产包）。

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
        print(f"launch failed: {exc}", file=sys.stderr)
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
