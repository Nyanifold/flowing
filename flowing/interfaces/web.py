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
      <span class="spacer"></span>
      <select id="model-select" title="模型选择" hidden></select>
      <span id="busy" hidden>生成中…</span>
    </header>
    <div id="record-view">
      <div id="conversation"></div>
      <div id="empty-state" hidden><p>选择左侧一个 Agent，或新建。</p></div>
    </div>
    <div id="tree-view" hidden></div>
    <pre id="meta" hidden></pre>
    <footer>
      <div id="ctx-bar" title="上下文占用">
        <div id="ctx-fill"></div>
        <span id="ctx-label"></span>
      </div>
      <div id="input-row">
        <textarea id="input" rows="1" autofocus autocomplete="off"
                  placeholder="输入消息；以 / 开头使用 repl 指令（Enter 发送，Shift+Enter 换行）"
                  spellcheck="false"></textarea>
        <button id="btn-send">发送</button>
      </div>
    </footer>
  </main>
</div>
<script src="/assets/app.js"></script>
</body>
</html>
"""

_APP_JS = r"""
"use strict";
// Flowing 内置前端 v4：暗色聊天界面（风格与样式基于 kimi-code web 界面，
// 参考实现：apps/kimi-inspect 的 ChatView/index.css，kimi-code @ a4a7df2
// （2026-09-10）——仅借配色/卡片/折叠形态；工具特殊处理、右侧面板、权限
// 管理、plan/goal 等 coding 功能均未采纳）。新增：消息正文 Markdown 渲染、
// 思考折叠、工具调用/返回折叠（展开为 JSON 高亮）、上下文进度条、模型选择。
// 通道均为 serve HTTP API（不变）：
//  GET /agents、POST /agents、GET …/messages、GET …/tree、POST …/rewind、
//  GET …/models、PATCH …/model、GET …/status、POST …/message、
//  POST …/command、GET …/stream(SSE: thinking/delta/message/turn_end)。
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const state = {
  agents: [], current: null, es: null, view: "record",
  rows: new Map(), generating: null, running: false,
};
const storeKey = "flowing.web.agent";

// ---- Markdown 渲染（消息正文；极小实现：先转义再按规则替换，无 raw HTML）----
function mdInline(s) {
  return esc(s)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function renderMd(src) {
  const lines = String(src).split("\n");
  const out = [];
  let inCode = false, codeBuf = [], listBuf = null;
  const flushList = () => {
    if (listBuf) { out.push(`<ul>${listBuf.join("")}</ul>`); listBuf = null; }
  };
  const flushCode = () => {
    out.push(`<pre class="code"><code>${esc(codeBuf.join("\n"))}</code></pre>`);
    codeBuf = [];
  };
  for (const line of lines) {
    if (/^```/.test(line)) {
      if (inCode) { flushCode(); inCode = false; }
      else { flushList(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushList(); out.push(`<div class="md-h md-h${h[1].length}">${mdInline(h[2])}</div>`); continue; }
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (li) { (listBuf = listBuf || []).push(`<li>${mdInline(li[1])}</li>`); continue; }
    flushList();
    if (line.trim() === "") out.push('<div class="md-gap"></div>');
    else out.push(`<div>${mdInline(line)}</div>`);
  }
  if (inCode) flushCode();
  flushList();
  return out.join("");
}

// ---- JSON 高亮（工具调用/返回展开体；先 pretty-print 再逐 token 上色）-------
function hlJson(obj) {
  const text = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  return esc(text).replace(
    /(&quot;(?:\\.|[^&])*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g,
    (m, str, colon) => {
      if (str) return colon ? `<span class="j-key">${str}</span>${colon}`
                            : `<span class="j-str">${str}</span>`;
      if (/^(true|false|null)$/.test(m)) return `<span class="j-kw">${m}</span>`;
      return `<span class="j-num">${m}</span>`;
    });
}

// ---- 通用 DOM ---------------------------------------------------------------
function el(cls, tag = "div") { const d = document.createElement(tag); if (cls) d.className = cls; return d; }
function scrollDown() { const c = $("#conversation"); c.scrollTop = c.scrollHeight; }
function setBusy(on) { state.running = on; $("#busy").hidden = !on; }

// 可折叠卡（思考 / 工具）：header 行点击开合，body 默认收起
function foldCard(kind, title, sub) {
  const card = el(`fold ${kind}`);
  const head = el("fold-head");
  head.appendChild(el("fold-icon")).textContent = "▸";
  const t = el("fold-title"); t.textContent = title; head.appendChild(t);
  if (sub) { const s = el("fold-sub"); s.textContent = sub; head.appendChild(s); }
  const body = el("fold-body"); body.hidden = true;
  head.onclick = () => {
    body.hidden = !body.hidden;
    head.firstChild.textContent = body.hidden ? "▸" : "▾";
    card.classList.toggle("open", !body.hidden);
  };
  card.append(head, body);
  return { card, body };
}

// ---- 消息渲染 ---------------------------------------------------------------
function renderBlocks(m) {
  // 把一条消息渲染为节点列表（思考卡 / md 正文 / 工具调用卡 / 工具结果卡）
  const nodes = [];
  const texts = [];
  for (const b of m.content || []) {
    if (b.type === "thinking" && b.thinking) {
      const f = foldCard("thinking", "思考过程", "");
      f.body.innerHTML = `<div class="thinking-text">${esc(b.thinking)}</div>`;
      nodes.push(f.card);
    } else if (b.type === "tool_call") {
      const f = foldCard("toolcall", `调用 ${b.name || "tool"}`,
                         JSON.stringify(b.args || {}).slice(0, 80));
      f.body.innerHTML = `<pre class="json">${hlJson(b.args || {})}</pre>`;
      nodes.push(f.card);
    } else if (b.text) texts.push(b.text);
    else if (b.data !== undefined) texts.push("```json\n" + JSON.stringify(b.data, null, 2) + "\n```");
  }
  if (texts.join("").trim()) {
    const body = el("md");
    body.innerHTML = renderMd(texts.join("\n"));
    nodes.push(body);
  }
  return nodes;
}

function renderMessage(m) {
  // 一条消息的完整行容器；返回 {row, key}
  const key = String(m.id);
  let row = state.rows.get(key);
  if (!row) { row = el(""); $("#conversation").appendChild(row); state.rows.set(key, row); }
  row.innerHTML = "";
  row.className = "row " + (m.kind === "user" ? "row-user" : "");
  if (m.kind === "tool") {
    const f = foldCard("toolresult", `工具返回（${m.tool_status || "?"}）`, "");
    const blocks = (m.content || []).map((b) =>
      b.data !== undefined ? b.data : (b.text || "")).join("\n");
    f.body.innerHTML = `<pre class="json">${hlJson(typeof blocks === "string" ? blocks : blocks)}</pre>`;
    row.appendChild(f.card);
  } else if (m.kind === "user") {
    const b = el("bubble bubble-user"); b.textContent =
      (m.content || []).map((x) => x.text || "").join("");
    row.appendChild(b);
  } else if (m.kind === "provider") {
    const wrap = el("bubble-group");
    for (const n of renderBlocks(m)) wrap.appendChild(n);
    row.appendChild(wrap);
  } else {   // event / system / 其余：小号 meta 行
    const d = el("msg-meta");
    d.textContent = `[${m.kind}${m.source ? ":" + m.source : ""}] ` +
      (m.content || []).map((x) => x.text || "").join("").slice(0, 200);
    row.appendChild(d);
  }
  return row;
}

// ---- 侧边栏：agent 继承树 ----------------------------------------------------
function renderTree() {
  const root = $("#agent-tree");
  root.innerHTML = "";
  const children = new Map();
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
      const li = el(a.agent_id === state.current ? "current" : "", "li");
      const row = el("agent");
      const kids = build(a.agent_id);
      const toggle = el("toggle", "span");
      if (kids) {
        toggle.textContent = "▾";
        toggle.onclick = (e) => {
          e.stopPropagation();
          kids.hidden = !kids.hidden;
          toggle.textContent = kids.hidden ? "▸" : "▾";
        };
      }
      row.appendChild(toggle);
      const label = el("label", "span");
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
  else { const d = el("msg-meta"); d.textContent = "(no agents)"; root.appendChild(d); }
}

async function refreshAgents() {
  const resp = await fetch("/agents");
  if (!resp.ok) return;
  state.agents = await resp.json();
  renderTree();
  if (!state.current && state.agents.length) focus(state.agents[0].agent_id);
}

// ---- 模型选择 / 上下文进度条 --------------------------------------------------
async function refreshModels() {
  if (!state.current) return;
  const resp = await fetch(`/agents/${encodeURIComponent(state.current)}/models`);
  if (!resp.ok) return;
  const b = await resp.json();
  const sel = $("#model-select");
  sel.innerHTML = "";
  for (const name of b.model_tags || []) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name;
    if (name === b.current) o.selected = true;
    sel.appendChild(o);
  }
  sel.hidden = !(b.model_tags || []).length;
}

async function refreshCtx() {
  if (!state.current) return;
  const resp = await fetch(`/agents/${encodeURIComponent(state.current)}/status`);
  if (!resp.ok) return;
  const snap = await resp.json();
  const cu = snap.context_usage || {};
  const fill = $("#ctx-fill"), label = $("#ctx-label");
  const ratio = cu.usage_ratio;
  if (ratio == null) { fill.style.width = "0%"; label.textContent = "ctx n/a"; return; }
  const pct = Math.min(100, Math.round(ratio * 100));
  fill.style.width = pct + "%";
  fill.dataset.hot = ratio > 0.8 ? "1" : "";
  label.textContent = `${(cu.tokens / 1000).toFixed(1)}k / ${((cu.context_window || 0) / 1000).toFixed(0)}k (${pct}%)`;
}

// ---- 当前 agent 视角 ---------------------------------------------------------
async function focus(id) {
  state.current = id;
  localStorage.setItem(storeKey, id);
  $("#current-id").textContent = id;
  $("#empty-state").hidden = true;
  renderTree();
  openStream(id);
  await Promise.all([showView("record"), refreshModels(), refreshCtx()]);
}

function closeStream() { if (state.es) { state.es.close(); state.es = null; } state.generating = null; }

function openStream(id) {
  closeStream();
  const es = new EventSource(`/agents/${encodeURIComponent(id)}/stream`);
  const conv = $("#conversation");
  es.addEventListener("thinking", (e) => {
    const d = JSON.parse(e.data);
    const key = String(d.message_id || "g" + state.generatingIndex());
    let row = state.rows.get(key);
    if (!row || !row.querySelector(".fold.thinking")) {
      if (!row) { row = el("row"); conv.appendChild(row); state.rows.set(key, row); }
      const f = foldCard("thinking", "思考过程", "");
      f.body.innerHTML = '<div class="thinking-text"></div>';
      row.appendChild(f.card);
    }
    const t = row.querySelector(".thinking-text");
    t.textContent += d.text || "";
    setBusy(true); scrollDown();
  });
  es.addEventListener("delta", (e) => {
    const d = JSON.parse(e.data);
    const key = String(d.message_id || "g" + state.generatingIndex());
    let row = state.rows.get(key);
    if (!row) { row = el("row"); conv.appendChild(row); state.rows.set(key, row); }
    let live = row.querySelector(".md.live");
    if (!live) {
      const wrap = el("bubble-group");
      live = el("md live"); wrap.appendChild(live); row.appendChild(wrap);
    }
    live.dataset.raw = (live.dataset.raw || "") + (d.text || "");
    live.textContent = live.dataset.raw;   // 流式期间纯文本，落定后整体 md 重排
    state.generating = row;
    setBusy(true); scrollDown();
  });
  es.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    setBusy(false);
    if (state.rows.has(String(m.id))) renderMessage(m);   // 流式行整体重排为最终形态
    else if (m.kind !== "user") renderMessage(m);         // user 已在发送时上屏
    scrollDown();
  });
  es.addEventListener("turn_end", () => {
    setBusy(false); state.generating = null; refreshCtx();
  });
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
  for (const m of msgs) renderMessage(m);
  scrollDown();
}

// ---- 「树」子页：消息树 + 切换分支 ---------------------------------------------
async function loadTree(id) {
  const host = $("#tree-view");
  host.innerHTML = "";
  const resp = await fetch(`/agents/${encodeURIComponent(id)}/tree`);
  if (!resp.ok) { const d = el("msg-meta"); d.textContent = "tree unavailable"; host.appendChild(d); return; }
  const t = await resp.json();
  const hdr = el("msg-meta");
  hdr.textContent = `head=${t.head}`;
  host.appendChild(hdr);
  function nodeEl(n) {
    const li = el(n.head ? "head" : "", "li");
    const d = el("node");
    d.textContent = `${n.kind}:${String(n.id).slice(0, 12)}${n.head ? " ←head" : ""}`;
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

// ---- 输入框：普通消息 vs repl 指令 ---------------------------------------------
async function runCommand(text) {
  const meta = $("#meta");
  meta.hidden = false;
  meta.textContent = "";
  const hd = el("msg-meta"); hd.textContent = "» " + text; meta.appendChild(hd);
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
      if (cmd === "model") refreshModels();
    } else {
      out.push("no agent focused (click one in the sidebar)");
    }
  } catch (err) { out.push("error: " + err.message); }
  for (const l of out) { const d = el("msg-meta"); d.textContent = l; meta.appendChild(d); }
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  if (text.startsWith("/")) { await runCommand(text); return; }
  if (!state.current) return;
  const userRow = el("row row-user");
  const b = el("bubble bubble-user"); b.textContent = text;
  userRow.appendChild(b);
  $("#conversation").appendChild(userRow);
  scrollDown();
  setBusy(true);
  const resp = await fetch(`/agents/${encodeURIComponent(state.current)}/message`,
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) });
  setBusy(false);
  if (!resp.ok) return;
  const body = await resp.json();
  const key = String(body.message_id);
  if (state.generating) {   // 流式行已被 SSE message 事件重排；此处只兜底
    state.generating = null;
  } else if (body.final_text && !state.rows.has(key + ":reply")) {
    const row = el("row");
    const wrap = el("bubble-group");
    const mdb = el("md"); mdb.innerHTML = renderMd(body.final_text);
    wrap.appendChild(mdb); row.appendChild(wrap);
    $("#conversation").appendChild(row);
  }
  refreshCtx();
}

// ---- 事件接线与启动 -------------------------------------------------------------
$("#btn-send").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("#input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
});
$("#model-select").addEventListener("change", async (e) => {
  if (!state.current) return;
  await fetch(`/agents/${encodeURIComponent(state.current)}/model`,
    { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_tag: e.target.value }) });
});
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
/* Flowing 内置前端样式——暗色主题，风格词汇基于 kimi-code web 界面
   （apps/kimi-inspect index.css / ChatView，kimi-code @ a4a7df2 2026-09-10）：
   #0b0d10 底、#d6dae0 文、neutral-800 边、sky-900/40 用户泡、折叠卡形态。 */
:root { color-scheme: dark; }
* { box-sizing: border-box; scrollbar-width: thin; scrollbar-color: #2b313a transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: #2b313a; border-radius: 4px; }
body {
  margin: 0; background: #0b0d10; color: #d6dae0; height: 100vh; display: flex;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
}
#layout { display: flex; width: 100%; height: 100%; }

/* 侧边栏 */
#sidebar { width: 240px; border-right: 1px solid #262b33; overflow: auto;
           padding: .5rem; background: #0e1116; }
.side-head { display: flex; justify-content: space-between; align-items: center;
             margin-bottom: .5rem; }
.brand { font-weight: 600; color: #e6e9ee; }
#btn-new { background: #1a1f27; color: #d6dae0; border: 1px solid #2b313a;
           border-radius: 6px; width: 22px; height: 22px; cursor: pointer; }
#btn-new:hover { border-color: #0ea5e9; }
#agent-tree ul { list-style: none; padding-left: 1em; margin: 0; }
#agent-tree .agent { cursor: pointer; padding: .2em .3em; border-radius: 6px;
                     display: flex; gap: .2em; align-items: center; color: #aab2bc; }
#agent-tree .agent:hover { background: #1a1f27; }
#agent-tree .current > .agent { background: rgba(14,116,144,.25); color: #e6e9ee; }
.toggle { display: inline-block; width: 1em; cursor: pointer; color: #6b7280; }

/* 主列 */
main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
header { display: flex; gap: .8rem; align-items: center; padding: .45rem 1rem;
         border-bottom: 1px solid #262b33; background: #0e1116; }
.spacer { flex: 1; }
#view-tabs .tab { border: 1px solid #2b313a; background: #11141a; color: #9aa1ab;
                  padding: .2em .7em; border-radius: 6px; cursor: pointer; font-size: 12px; }
#view-tabs .tab.active { background: rgba(14,116,144,.35); color: #e6f6ff;
                         border-color: #155e75; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 11px; color: #6b7280; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
#busy { color: #38bdf8; font-size: 12px; }
#model-select { background: #11141a; color: #d6dae0; border: 1px solid #2b313a;
                border-radius: 6px; padding: .15em .4em; font-size: 12px; }

/* 消息区 */
#record-view { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
#conversation { flex: 1; padding: 1rem 1.2rem; display: flex; flex-direction: column; }
.row { margin: .25rem 0; }
.row-user { display: flex; justify-content: flex-end; }
.bubble-user { max-width: 80%; white-space: pre-wrap; border-radius: 8px;
               background: rgba(14,116,144,.4); padding: .45rem .75rem;
               color: #f0f9ff; }
.bubble-group { max-width: 85%; display: flex; flex-direction: column; gap: .35rem; }
.msg-meta { margin: .15rem auto; color: #6b7280; font-size: 11px;
            font-family: ui-monospace, Menlo, monospace; white-space: normal; }

/* Markdown 正文 */
.md { line-height: 1.55; }
.md code { background: #1a1f27; border-radius: 4px; padding: 0 .3em;
           font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.md pre.code { background: #0e1116; border: 1px solid #262b33; border-radius: 8px;
               padding: .6rem .8rem; overflow-x: auto; }
.md pre.code code { background: none; padding: 0; }
.md a { color: #38bdf8; }
.md-h { font-weight: 600; color: #e6e9ee; margin: .35rem 0 .15rem; }
.md-h1 { font-size: 15px; } .md-h2 { font-size: 14px; }
.md-gap { height: .4rem; }
.md ul { margin: .2rem 0; padding-left: 1.3em; }

/* 折叠卡（思考 / 工具调用 / 工具返回） */
.fold { border: 1px solid #262b33; border-radius: 8px; background: rgba(17,20,26,.5);
        max-width: 85%; }
.fold-head { display: flex; align-items: center; gap: .4rem; padding: .35rem .6rem;
             cursor: pointer; user-select: none; }
.fold-head:hover { background: rgba(26,31,39,.6); border-radius: 8px; }
.fold-icon { color: #6b7280; font-size: 10px; width: 1em; }
.fold-title { font-size: 11px; color: #9aa1ab; font-family: ui-monospace, Menlo, monospace; }
.fold-sub { font-size: 10px; color: #525a66; font-family: ui-monospace, Menlo, monospace;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 40em; }
.fold-body { border-top: 1px solid #262b33; padding: .5rem .7rem; }
.fold.thinking { border-style: dashed; border-color: #3a3f4a; }
.fold.thinking .fold-title { color: #b7a05a; }
.thinking-text { white-space: pre-wrap; color: #9aa1ab; font-size: 12px; }
pre.json { margin: 0; font-family: ui-monospace, Menlo, monospace; font-size: 11px;
           overflow-x: auto; color: #c8cfd8; }
.j-key { color: #7dd3fc; } .j-str { color: #86efac; }
.j-num { color: #f0abfc; } .j-kw  { color: #fbbf24; }

/* 树子页 */
#tree-view { flex: 1; overflow: auto; padding: 1rem;
             font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
#tree-view ul { list-style: none; padding-left: 1.2em; }
#tree-view .node { cursor: pointer; padding: 1px 4px; border-radius: 4px;
                   color: #9aa1ab; }
#tree-view .node:hover { background: #1a1f27; }
#tree-view .head { color: #38bdf8; font-weight: 700; }

/* 底部：上下文进度条 + 输入 */
#meta { flex: 0 1 auto; max-height: 30%; overflow: auto; padding: .5rem 1rem;
        background: #0e1116; border-top: 1px solid #262b33; margin: 0; }
footer { padding: .5rem 1rem .7rem; border-top: 1px solid #262b33;
         background: #0e1116; }
#ctx-bar { position: relative; height: 14px; border: 1px solid #262b33;
           border-radius: 7px; background: #11141a; margin-bottom: .45rem;
           overflow: hidden; }
#ctx-fill { height: 100%; width: 0%; background: #155e75; transition: width .3s; }
#ctx-fill[data-hot="1"] { background: #b45309; }
#ctx-label { position: absolute; inset: 0; display: flex; align-items: center;
             justify-content: center; font-size: 10px; color: #9aa1ab;
             font-family: ui-monospace, Menlo, monospace; }
#input-row { display: flex; gap: .5rem; align-items: flex-end; }
#input { flex: 1; resize: none; border: 1px solid #2b313a; border-radius: 8px;
         background: #05070a; color: #f3f4f6; padding: .5rem .7rem;
         font: inherit; outline: none; max-height: 160px; }
#input:focus { border-color: #0284c7; }
#btn-send { background: rgba(14,116,144,.5); color: #e6f6ff;
            border: 1px solid #155e75; border-radius: 8px;
            padding: .45rem 1rem; cursor: pointer; }
#btn-send:hover { background: rgba(14,116,144,.75); }
#empty-state { text-align: center; margin-top: 4rem; color: #6b7280; }
"""
_ASSETS_CACHE: FrontendAssets | None = None


def get_frontend_assets() -> FrontendAssets:
    """返回前端资产包。

    ``web`` 子命令获取前端资产的唯一入口。返回框架随包发布的内置
    默认前端（暗色聊天界面，风格基于 kimi-code web 界面 @ a4a7df2）：
    agent 继承树侧栏 + 「记录 / 树」双子页（记录从当前 head 上溯，树可
    点节点切换分支），消息正文 Markdown 渲染、思考折叠、工具调用/返回
    折叠卡（展开为 JSON 高亮）、输入框上方上下文占用进度条、头部模型
    选择；输入框支持消息与 repl 指令（经 serve 的观察 / 控制端点）。

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
