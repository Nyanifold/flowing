import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, AgentInfo, Message, Tree, TreeNode, ContentBlock } from "./api";
import { Md, JsonView, FoldCard } from "./components";

const STORE_KEY = "flowing.web.agent";

// ---- 侧边栏（kimi 风格会话列表：圆角行、hover、选中蓝底、最近回复预览）---------
function Sidebar({ agents, current, onFocus, onNew }: {
  agents: AgentInfo[]; current: string | null;
  onFocus: (id: string) => void; onNew: () => void;
}) {
  const children = new Map<string | null, AgentInfo[]>();
  for (const a of agents) {
    const p = a.parent_agent_id || null;
    if (!children.has(p)) children.set(p, []);
    children.get(p)!.push(a);
  }
  const renderLevel = (parent: string | null, depth: number): React.ReactNode =>
    (children.get(parent) || [])
      .sort((a, b) => (a.name || a.agent_id).localeCompare(b.name || b.agent_id))
      .map((a) => (
        <div key={a.agent_id}>
          <button type="button" onClick={() => onFocus(a.agent_id)}
            className={`mb-0.5 w-full rounded-lg px-2 py-1.5 text-left transition-colors ${
              a.agent_id === current
                ? "bg-[var(--soft)] text-[#0b4a8f]"
                : "text-[#292929] hover:bg-[var(--panel2)]"}`}
            style={{ paddingLeft: `${8 + depth * 14}px` }}>
            <div className="truncate text-[12.5px] font-medium">{a.name || a.agent_id}</div>
            <div className="mono truncate text-[10px] text-[var(--faint)]">
              {a.agent_id}{a.last_reply ? ` · ${a.last_reply}` : ""}
            </div>
          </button>
          {renderLevel(a.agent_id, depth + 1)}
        </div>
      ));
  return (
    <aside className="flex w-[240px] flex-col border-r border-[var(--line)] bg-[var(--panel)]">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[13px] font-semibold text-[#121212]">Flowing</span>
        <button type="button" onClick={onNew} title="新建 Agent"
          className="h-[22px] w-[22px] rounded-md border border-[var(--line)] bg-white text-[#292929] hover:border-[var(--blue)]">
          +
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">{renderLevel(null, 0)}</div>
    </aside>
  );
}

// ---- 消息行 -------------------------------------------------------------------
function toolText(m: Message): unknown {
  const parts = (m.content || []).map((b) => (b.data !== undefined ? b.data : b.text || ""));
  return parts.length === 1 ? parts[0] : parts.join("\n");
}

function MessageRow({ m }: { m: Message }) {
  if (m.kind === "user") {
    return (
      <div className="my-1 flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-[var(--soft)] px-3 py-2 text-[13px] text-[#0b3a6e]">
          {(m.content || []).map((b) => b.text || "").join("")}
        </div>
      </div>
    );
  }
  if (m.kind === "tool") {
    return (
      <div className="my-1">
        <FoldCard title={`工具返回（${m.tool_status || "?"}）`}>
          <JsonView value={toolText(m)} />
        </FoldCard>
      </div>
    );
  }
  if (m.kind === "provider") {
    const nodes: React.ReactNode[] = [];
    const texts: string[] = [];
    (m.content || []).forEach((b: ContentBlock, i: number) => {
      if (b.type === "thinking" && b.thinking) {
        nodes.push(
          <FoldCard key={i} tone="thinking" title="思考过程">
            <div className="whitespace-pre-wrap text-[12px] text-[var(--dim)]">{b.thinking}</div>
          </FoldCard>);
      } else if (b.type === "tool_call") {
        nodes.push(
          <FoldCard key={i} title={`调用 ${b.name || "tool"}`}
            sub={JSON.stringify(b.args || {}).slice(0, 80)}>
            <JsonView value={b.args || {}} />
          </FoldCard>);
      } else if (b.text) texts.push(b.text);
      else if (b.data !== undefined) texts.push("```json\n" + JSON.stringify(b.data, null, 2) + "\n```");
    });
    return (
      <div className="my-1">
        <div className="flex max-w-[85%] flex-col gap-1.5">
          {nodes}
          {texts.join("").trim() && <Md text={texts.join("\n")} />}
        </div>
      </div>
    );
  }
  // event / system / 其余：小号 meta 行
  return (
    <div className="mono mx-auto my-0.5 text-[10.5px] text-[var(--faint)]">
      [{m.kind}{m.source ? ":" + m.source : ""}]{" "}
      {(m.content || []).map((b) => b.text || "").join("").slice(0, 200)}
    </div>
  );
}

/** 不在当前链上的消息（折叠分叉体内）：预览行——每条消息都有预览。 */
function PreviewRow({ n }: { n: TreeNode }) {
  return (
    <div className="mono my-0.5 truncate text-[10.5px] text-[var(--faint)]">
      {n.kind}:{String(n.id).slice(0, 12)}　{n.preview}
    </div>
  );
}

// ---- 记录视图（消息树：仅分叉处缩进；分叉首条消息为可开合的折叠头）--------------
function RecordView({ tree, byId }: { tree: Tree; byId: Map<string, Message> }) {
  const parentOf = new Map<string, string | null>();
  (function collect(nodes: TreeNode[], parent: string | null) {
    for (const n of nodes) { parentOf.set(n.id, parent); collect(n.children || [], n.id); }
  })(tree.roots, null);
  const onPath = new Set<string>();
  for (let cur: string | null = tree.head; cur != null; cur = parentOf.get(cur) ?? null) onPath.add(cur);

  const renderSeq = (nodes: TreeNode[], keyPrefix: string): React.ReactNode => {
    const out: React.ReactNode[] = [];
    let list = nodes;
    let guard = 0;
    while (list.length && guard++ < 10000) {
      if (list.length === 1) {
        const n = list[0];
        const m = byId.get(String(n.id));
        out.push(m ? <MessageRow key={n.id} m={m} /> : <PreviewRow key={n.id} n={n} />);
        list = n.children || [];
        continue;
      }
      // 分叉：每支一个折叠段（首条消息为折叠头），当前路径所在支默认展开
      out.push(
        <div key={`${keyPrefix}-fork-${guard}`} className="my-1 ml-3 border-l-2 border-[var(--bd)] pl-2">
          {list.map((branch) => {
            const onThis = onPath.has(branch.id);
            const bm = byId.get(String(branch.id));
            return (
              <FoldCard key={branch.id} defaultOpen={onThis}
                title={`分支 ${branch.kind}:${String(branch.id).slice(0, 12)}`}
                sub={branch.preview}>
                {bm ? <MessageRow m={bm} /> : <PreviewRow n={branch} />}
                {onThis && renderSeq(branch.children || [], `${keyPrefix}-${branch.id}`)}
              </FoldCard>
            );
          })}
        </div>);
      return out;
    }
    return out;
  };
  return <>{renderSeq(tree.roots, "root")}</>;
}

// ---- 树子页（只读结构 + 每条消息预览 + 点击切分支） ------------------------------
function TreeView({ tree, onRewind }: { tree: Tree; onRewind: (id: string) => void }) {
  const nodeEl = (n: TreeNode): React.ReactNode => (
    <li key={n.id}>
      <button type="button" title={n.id} onClick={() => onRewind(n.id)}
        className={`mono rounded px-1 py-0.5 text-left text-[11px] hover:bg-[var(--panel2)] ${
          n.head ? "font-bold text-[var(--blue)]" : "text-[var(--dim)]"}`}>
        {n.kind}:{String(n.id).slice(0, 12)}{n.head ? " ←head" : ""}
        <span className="ml-2 font-normal text-[var(--faint)]">{n.preview}</span>
      </button>
      {n.children?.length > 0 && (
        <ul className="ml-4 list-none border-l border-[var(--line)] pl-2">
          {n.children.map(nodeEl)}
        </ul>
      )}
    </li>
  );
  return (
    <div className="flex-1 overflow-auto p-4">
      <div className="mono mb-2 text-[10.5px] text-[var(--faint)]">head={tree.head}</div>
      <ul className="list-none">{tree.roots.map(nodeEl)}</ul>
    </div>
  );
}

// ---- 主界面 ---------------------------------------------------------------------
export default function App() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [view, setView] = useState<"record" | "tree">("record");
  const [byId, setById] = useState<Map<string, Message>>(new Map());
  const [tree, setTree] = useState<Tree | null>(null);
  const [models, setModels] = useState<{ current: string | null; model_tags: string[] }>({ current: null, model_tags: [] });
  const [ctx, setCtx] = useState<{ tokens: number; usage_ratio: number | null; context_window: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [metaLines, setMetaLines] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const convRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const liveRef = useRef<Map<string, { thinking: string; text: string }>>(new Map());
  const [, forceLive] = useState(0);

  const scrollDown = () => {
    requestAnimationFrame(() => {
      if (convRef.current) convRef.current.scrollTop = convRef.current.scrollHeight;
    });
  };

  const loadRecord = useCallback(async (id: string) => {
    const [msgs, t] = await Promise.all([api.messages(id), api.tree(id)]);
    setById(new Map(msgs.map((m) => [String(m.id), m])));
    setTree(t);
    scrollDown();
  }, []);

  const refreshCtx = useCallback(async (id: string) => {
    try { setCtx((await api.status(id)).context_usage ?? null); } catch { /* 状态不可达 */ }
  }, []);

  const refreshModels = useCallback(async (id: string) => {
    try { setModels(await api.models(id)); } catch { setModels({ current: null, model_tags: [] }); }
  }, []);

  // SSE：流式增量进 liveRef，message 事件触发记录重载
  const openStream = useCallback((id: string) => {
    esRef.current?.close();
    liveRef.current.clear();
    const es = new EventSource(`/agents/${encodeURIComponent(id)}/stream`);
    es.addEventListener("thinking", (e) => {
      const d = JSON.parse(e.data);
      const cur = liveRef.current.get(d.message_id) || { thinking: "", text: "" };
      cur.thinking += d.text || "";
      liveRef.current.set(d.message_id, cur);
      setBusy(true); forceLive((n) => n + 1); scrollDown();
    });
    es.addEventListener("delta", (e) => {
      const d = JSON.parse(e.data);
      const cur = liveRef.current.get(d.message_id) || { thinking: "", text: "" };
      cur.text += d.text || "";
      liveRef.current.set(d.message_id, cur);
      setBusy(true); forceLive((n) => n + 1); scrollDown();
    });
    es.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      liveRef.current.delete(String(m.id));
      setBusy(false);
      loadRecord(id);   // 落定后整体重载（分叉结构可能变化）
    });
    es.addEventListener("turn_end", () => { setBusy(false); refreshCtx(id); });
    es.onerror = () => { es.close(); setBusy(false); };
    esRef.current = es;
  }, [loadRecord, refreshCtx]);

  const focus = useCallback(async (id: string) => {
    setCurrent(id);
    localStorage.setItem(STORE_KEY, id);
    openStream(id);
    await Promise.all([loadRecord(id), refreshModels(id), refreshCtx(id)]);
  }, [openStream, loadRecord, refreshModels, refreshCtx]);

  const refreshAgents = useCallback(async () => {
    const list = await api.agents();
    setAgents(list);
    if (!current && list.length) focus(list[0].agent_id);
  }, [current, focus]);

  useEffect(() => {
    refreshAgents().then(() => {
      const saved = localStorage.getItem(STORE_KEY);
      if (saved) api.agents().then((list) => {
        if (list.some((a) => a.agent_id === saved)) focus(saved);
      });
    });
    return () => esRef.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newAgent = async () => {
    const b = await api.createAgent();
    await refreshAgents();
    await focus(b.agent_id);
  };

  const runCommand = async (text: string) => {
    const out: string[] = ["» " + text];
    const [cmd, ...rest] = text.slice(1).split(/\s+/);
    const arg = rest.join(" ").trim();
    try {
      if (cmd === "new") { await newAgent(); out.push("bound (new agent)"); }
      else if (cmd === "agent") {
        const hit = agents.find((a) => a.agent_id === arg || a.name === arg);
        if (hit) { await focus(hit.agent_id); out.push("focus " + hit.agent_id); }
        else out.push("unknown agent: " + arg);
      } else if (current) {
        const b = await api.command(current, text);
        out.push(...(b.lines || []));
        if (cmd === "model") refreshModels(current);
      } else out.push("no agent focused (click one in the sidebar)");
    } catch (err) { out.push("error: " + (err as Error).message); }
    setMetaLines(out);
  };

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    if (text.startsWith("/")) { await runCommand(text); return; }
    if (!current) return;
    setBusy(true);
    try { await api.send(current, text); } finally { setBusy(false); }
    refreshCtx(current);
    // 新消息经 SSE message 事件落行（loadRecord 重载）；user 行由重载兜底
    loadRecord(current);
  };

  const rewind = async (mid: string) => {
    if (!current) return;
    await api.rewind(current, mid);
    await loadRecord(current);
    setView("record");
  };

  const live = [...liveRef.current.entries()];
  const ratio = ctx?.usage_ratio ?? null;
  const pct = ratio == null ? 0 : Math.min(100, Math.round(ratio * 100));

  return (
    <div className="flex h-screen w-full">
      <Sidebar agents={agents} current={current} onFocus={focus} onNew={newAgent} />
      <main className="flex min-w-0 flex-1 flex-col bg-[var(--bg)]">
        <header className="flex items-center gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-2">
          <span>
            {(["record", "tree"] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)}
                className={`mr-1 rounded-md border px-2.5 py-1 text-[12px] ${
                  view === v
                    ? "border-[var(--bd)] bg-[var(--soft)] text-[#0b4a8f]"
                    : "border-[var(--line)] bg-white text-[var(--muted)]"}`}>
                {v === "record" ? "记录" : "树"}
              </button>
            ))}
          </span>
          <span className="mono text-[11px] text-[var(--faint)]">{current || ""}</span>
          <span className="flex-1" />
          {models.model_tags.length > 0 && (
            <select value={models.current || ""} title="模型选择"
              onChange={(e) => current && api.setModel(current, e.target.value).then(() => refreshModels(current))}
              className="rounded-md border border-[var(--line)] bg-white px-1.5 py-1 text-[12px] text-[#292929]">
              {models.model_tags.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          {busy && <span className="text-[12px] text-[var(--blue)]">生成中…</span>}
        </header>

        {view === "record" ? (
          <div ref={convRef} className="flex-1 overflow-y-auto px-5 py-3">
            {tree && <RecordView tree={tree} byId={byId} />}
            {live.map(([mid, buf]) => (
              <div key={mid} className="my-1 flex max-w-[85%] flex-col gap-1.5">
                {buf.thinking && (
                  <FoldCard tone="thinking" title="思考过程" defaultOpen>
                    <div className="whitespace-pre-wrap text-[12px] text-[var(--dim)]">{buf.thinking}</div>
                  </FoldCard>)}
                {buf.text && <div className="whitespace-pre-wrap text-[13px]">{buf.text}</div>}
              </div>
            ))}
            {!current && <p className="mt-16 text-center text-[var(--faint)]">选择左侧一个 Agent，或新建。</p>}
          </div>
        ) : (
          tree && <TreeView tree={tree} onRewind={rewind} />
        )}

        {metaLines.length > 0 && (
          <pre className="mono max-h-[30%] overflow-auto border-t border-[var(--line)] bg-[var(--panel)] px-4 py-2 text-[11px] text-[var(--dim)]">
            {metaLines.join("\n")}
          </pre>
        )}

        <footer className="border-t border-[var(--line)] bg-[var(--panel)] px-4 pb-3 pt-2">
          <div className="relative mb-2 h-[14px] overflow-hidden rounded-full border border-[var(--line)] bg-white"
            title="上下文占用">
            <div className="h-full transition-[width] duration-300"
              style={{ width: `${pct}%`, background: ratio != null && ratio > 0.8 ? "#d29922" : "#1783ff" }} />
            <span className="mono absolute inset-0 flex items-center justify-center text-[10px] text-[var(--muted)]">
              {ratio == null
                ? "ctx n/a"
                : `${((ctx?.tokens ?? 0) / 1000).toFixed(1)}k / ${((ctx?.context_window ?? 0) / 1000).toFixed(0)}k (${pct}%)`}
            </span>
          </div>
          <div className="flex items-end gap-2">
            <textarea value={input} rows={1} autoFocus spellCheck={false}
              placeholder="输入消息；以 / 开头使用 repl 指令（Enter 发送，Shift+Enter 换行）"
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              className="max-h-[160px] flex-1 resize-none rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-[13px] text-[#121212] outline-none focus:border-[var(--blue)]" />
            <button type="button" onClick={send}
              className="rounded-lg border border-[var(--bd)] bg-[var(--soft)] px-4 py-2 text-[13px] text-[#0b4a8f] hover:bg-[#d6e9ff]">
              发送
            </button>
          </div>
        </footer>
      </main>
    </div>
  );
}
