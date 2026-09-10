import React, { useCallback, useEffect, useRef, useState } from "react";
import { PlusIcon, ArrowUpIcon, SquareIcon, Loader2Icon } from "lucide-react";
import { api, AgentInfo, Message, Tree, TreeNode } from "./api";
import { Reasoning, ToolCard, MessageResponse, cn } from "./components";

const STORE_KEY = "flowing.web.agent";

/** 窗口容量格式化：>=1M 以 M 显示，小数位非 0 时保留一位（1M / 1.5M）。 */
function fmtWindow(w: number): string {
  if (w >= 1_000_000) {
    const m = w / 1_000_000;
    return (m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)) + "M";
  }
  return (w / 1000).toFixed(0) + "k";
}

// ---- 侧边栏（对应 kimi sessions 侧栏：圆角行 + 标题 + 次级预览行，选中态
// bg-accent） ---------------------------------------------------------------------
function Sidebar({ agents, current, onFocus, onNew }: {
  agents: AgentInfo[]; current: string | null;
  onFocus: (id: string) => void; onNew: () => void;
}) {
  const children = new Map<string | null, AgentInfo[]>();
  for (const a of agents) {
    // 根 Agent 的 parent_agent_id 是 Runtime 的 node_id（"runtime-0"），
    // 归一化为 null 作为侧栏根层
    const p = a.parent_agent_id && a.parent_agent_id !== "runtime-0" ? a.parent_agent_id : null;
    if (!children.has(p)) children.set(p, []);
    children.get(p)!.push(a);
  }
  const renderLevel = (parent: string | null, depth: number): React.ReactNode =>
    (children.get(parent) || [])
      .sort((a, b) => (a.name || a.agent_id).localeCompare(b.name || b.agent_id))
      .map((a) => (
        <div key={a.agent_id}>
          <button type="button" onClick={() => onFocus(a.agent_id)}
            className={cn(
              "mb-0.5 w-full rounded-md px-2 py-1.5 text-left transition-colors",
              a.agent_id === current
                ? "bg-[var(--accent)] text-[var(--accent-foreground)]"
                : "text-[var(--foreground)] hover:bg-[var(--accent)]/60")}
            style={{ paddingLeft: `${8 + depth * 14}px` }}>
            <div className="truncate text-[12.5px] font-medium">{a.name || a.agent_id}</div>
            <div className="mono truncate text-[10px] text-[var(--muted-foreground)]">
              {a.agent_id}{a.last_reply ? ` · ${a.last_reply}` : ""}
            </div>
          </button>
          {renderLevel(a.agent_id, depth + 1)}
        </div>
      ));
  return (
    <aside className="flex w-[248px] flex-col border-r border-[var(--border)] bg-[var(--muted)]/40">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[13px] font-semibold">Flowing</span>
        <button type="button" onClick={onNew} title="新建 Agent"
          className="flex size-[22px] items-center justify-center rounded-md border border-[var(--border)] bg-white hover:border-[var(--primary)]">
          <PlusIcon className="size-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">{renderLevel(null, 0)}</div>
    </aside>
  );
}

// ---- 消息行（组织对应 kimi assistant-message：思考 Reasoning / 工具 ToolCard /
// 正文 MessageResponse；工具调用与返回按 tool_call_id 配对进同一张卡） -----------
function MessageRow({ m, toolResults }: { m: Message; toolResults: Map<string, Message> }) {
  if (m.kind === "user") {
    return (
      <div className="my-1 flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-[#e8f3ff] px-3 py-2 text-[13px] text-[#0b3a6e]">
          {(m.content || []).map((b) => b.text || "").join("")}
        </div>
      </div>
    );
  }
  if (m.kind === "tool") return null;   // 结果并入对应 ToolCall 卡，不单独成行
  if (m.kind === "provider") {
    const nodes: React.ReactNode[] = [];
    const texts: string[] = [];
    (m.content || []).forEach((b, i) => {
      if (b.type === "thinking" && b.thinking) {
        nodes.push(<Reasoning key={i} text={b.thinking} />);
      } else if (b.type === "tool_call") {
        const res = toolResults.get(b.id || "");
        const out = res
          ? res.content.map((x) => (x.data !== undefined ? x.data : x.text || ""))
          : undefined;
        nodes.push(
          <ToolCard key={i} name={b.name || "tool"} args={b.args || {}}
            status={res ? res.tool_status : "pending"}
            output={out ? (out.length === 1 ? out[0] : out.join("\n")) : undefined} />);
      } else if (b.text) texts.push(b.text);
      else if (b.data !== undefined) texts.push("```json\n" + JSON.stringify(b.data, null, 2) + "\n```");
    });
    return (
      <div className="my-1.5 flex max-w-[92%] flex-col gap-1">
        {nodes}
        {texts.join("").trim() !== "" && <MessageResponse text={texts.join("\n")} />}
      </div>
    );
  }
  return (
    <div className="mono mx-auto my-0.5 text-[10.5px] text-[var(--muted-foreground)]">
      [{m.kind}{m.source ? ":" + m.source : ""}]{" "}
      {(m.content || []).map((b) => b.text || "").join("").slice(0, 200)}
    </div>
  );
}

// ---- 记录视图（纯链：只展示从当前 head 上溯的消息序列，不含任何树的
// 元素——分叉/分支只出现在「树」页签） -------------------------------------------
function RecordView({ messages }: { messages: Message[] }) {
  // tool_call_id → TOOL 消息（当前链配对表）
  const toolResults = new Map<string, Message>();
  for (const m of messages)
    if (m.kind === "tool" && (m as unknown as { tool_call_id?: string }).tool_call_id)
      toolResults.set((m as unknown as { tool_call_id: string }).tool_call_id, m);
  return (
    <>
      {messages
        .filter((m) => m.kind !== "tool")   // 结果并入 ToolCard，不单独成行
        .map((m) => <MessageRow key={m.id} m={m} toolResults={toolResults} />)}
    </>
  );
}

// ---- 树子页（DFS 序排列；分叉首条消息带展开/坍缩箭头，箭头槽即缩进锚点；
// 不分叉的线性链不缩进、与分叉头同位对齐） -------------------------------------
function TreeView({ tree, onRewind }: { tree: Tree; onRewind: (id: string) => void }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  const Row = ({ n, forkHead }: { n: TreeNode; forkHead: boolean }) => {
    const hasKids = (n.children?.length ?? 0) > 0;
    const isCollapsed = collapsed.has(n.id);
    return (
      <div className="flex items-center gap-0.5">
        {/* 只有「分叉出的首条消息」渲染箭头（有子消息才可开合）；线性链
            不占箭头槽、不缩进 */}
        {forkHead && hasKids ? (
          <button type="button" onClick={() => toggle(n.id)} title={isCollapsed ? "展开" : "坍缩"}
            className="flex w-[16px] shrink-0 items-center justify-center rounded text-[10px] font-bold text-[var(--primary)] hover:bg-[var(--accent)]">
            {isCollapsed ? "▸" : "▾"}
          </button>
        ) : forkHead ? (
          <span className="w-[16px] shrink-0" />
        ) : null}
        <button type="button" title={n.id} onClick={() => onRewind(n.id)}
          className={cn("mono rounded px-1 py-0.5 text-left text-[11px] hover:bg-[var(--accent)]",
            n.head ? "font-bold text-[var(--primary)]" : "text-[var(--muted-foreground)]",
            forkHead && "text-[var(--foreground)]")}>
          {n.kind}:{String(n.id).slice(0, 12)}{n.head ? " ←head" : ""}
          <span className="ml-2 font-normal text-[var(--muted-foreground)]">{n.preview}</span>
        </button>
      </div>
    );
  };

  const renderNodes = (nodes: TreeNode[]): React.ReactNode => {
    if (nodes.length === 0) return null;
    if (nodes.length === 1) {
      // 线性链：同位渲染，不缩进
      const n = nodes[0];
      return (
        <div key={n.id}>
          <Row n={n} forkHead={false} />
          {renderNodes(n.children || [])}
        </div>
      );
    }
    // 分叉：每条分支的首条消息带箭头；其子树缩进一格（对齐箭头后文字起点）
    return nodes.map((n) => {
      const isCollapsed = collapsed.has(n.id);
      return (
        <div key={n.id}>
          <Row n={n} forkHead />
          {!isCollapsed && (
            <div className="ml-[16px]">{renderNodes(n.children || [])}</div>
          )}
        </div>
      );
    });
  };

  return (
    <div className="flex-1 overflow-auto p-4">
      <div className="mono mb-2 text-[10.5px] text-[var(--muted-foreground)]">head={tree.head}</div>
      {renderNodes(tree.roots)}
    </div>
  );
}

// ---- 主界面（头部 = chat-workspace-header 形态；底部 = prompt-composer 形态）------
export default function App() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [view, setView] = useState<"record" | "tree">("record");
  const [messages, setMessages] = useState<Message[]>([]);
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
    setMessages(msgs);
    setTree(t);
    scrollDown();
  }, []);

  const refreshCtx = useCallback(async (id: string) => {
    try { setCtx((await api.status(id)).context_usage ?? null); } catch { /* 状态不可达 */ }
  }, []);

  const refreshModels = useCallback(async (id: string) => {
    try { setModels(await api.models(id)); } catch { setModels({ current: null, model_tags: [] }); }
  }, []);

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
      loadRecord(id);
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
    loadRecord(current);
  };

  const rewind = async (mid: string) => {
    if (!current) return;
    await api.rewind(current, mid);
    await loadRecord(current);
    setView("record");
  };

  const live = [...liveRef.current.entries()];
  // usage_ratio 缺失时前端兜底：tokens / context_window（serve 已补字段，
  // 旧版 serve 无此字段时此处兜底）
  const ratio = ctx?.usage_ratio
    ?? (ctx?.context_window ? ctx.tokens / ctx.context_window : null);
  const pct = ratio == null ? 0 : Math.min(100, Math.round(ratio * 100));

  return (
    <div className="flex h-screen w-full">
      <Sidebar agents={agents} current={current} onFocus={focus} onNew={newAgent} />
      <main className="flex min-w-0 flex-1 flex-col bg-[var(--background)]">
        {/* 头部（chat-workspace-header 形态） */}
        <header className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-2">
          <span>
            {(["record", "tree"] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)}
                className={cn("mr-1 rounded-md border px-2.5 py-1 text-[12px]",
                  view === v
                    ? "border-[var(--primary)]/30 bg-[var(--accent)] text-[var(--accent-foreground)]"
                    : "border-[var(--border)] bg-white text-[var(--muted-foreground)]")}>
                {v === "record" ? "记录" : "树"}
              </button>
            ))}
          </span>
          <span className="mono text-[11px] text-[var(--muted-foreground)]">{current || ""}</span>
          <span className="flex-1" />
          {models.model_tags.length > 0 && (
            <select value={models.current || ""} title="模型选择"
              onChange={(e) => current && api.setModel(current, e.target.value).then(() => refreshModels(current))}
              className="rounded-md border border-[var(--input)] bg-white px-1.5 py-1 text-[12px]">
              {models.model_tags.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          {busy && (
            <span className="flex items-center gap-1 text-[12px] text-[var(--primary)]">
              <Loader2Icon className="size-3 animate-spin" />生成中…
            </span>
          )}
        </header>

        {view === "record" ? (
          <div ref={convRef} className="flex-1 overflow-y-auto px-5 py-3">
            <RecordView messages={messages} />
            {live.map(([mid, buf]) => (
              <div key={mid} className="my-1.5 flex max-w-[92%] flex-col gap-1">
                {buf.thinking && <Reasoning text={buf.thinking} streaming />}
                {buf.text && <MessageResponse text={buf.text} streaming />}
              </div>
            ))}
            {!current && <p className="mt-16 text-center text-[var(--muted-foreground)]">选择左侧一个 Agent，或新建。</p>}
          </div>
        ) : (
          tree && <TreeView tree={tree} onRewind={rewind} />
        )}

        {metaLines.length > 0 && (
          <pre className="mono max-h-[30%] overflow-auto border-t border-[var(--border)] bg-[var(--muted)]/50 px-4 py-2 text-[11px] text-[var(--muted-foreground)]">
            {metaLines.join("\n")}
          </pre>
        )}

        {/* 底部（chat-prompt-composer 形态：上下文进度条 + 圆角输入条） */}
        <footer className="px-4 pb-3 pt-1">
          <div className="relative mb-1.5 h-[14px] overflow-hidden rounded-full border border-[var(--border)] bg-white"
            title="上下文占用">
            <div className="h-full transition-[width] duration-300"
              style={{ width: `${pct}%`, background: ratio != null && ratio > 0.8 ? "var(--warning)" : "var(--primary)" }} />
            <span className="mono absolute inset-0 flex items-center justify-center text-[10px] text-[var(--muted-foreground)]">
              {ratio == null
                ? "ctx n/a"
                : `${((ctx?.tokens ?? 0) / 1000).toFixed(1)}k / ${fmtWindow(ctx?.context_window ?? 0)} (${pct}%)`}
            </span>
          </div>
          <div className="flex items-end gap-2 rounded-xl border border-[var(--input)] bg-white p-2 shadow-sm focus-within:border-[var(--ring)]">
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
              className="max-h-[160px] flex-1 resize-none bg-transparent px-1 py-1 text-[13px] outline-none" />
            {busy ? (
              <button type="button" title="取消当前回合"
                onClick={() => current && api.command(current, "/cancel")}
                className="flex size-8 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]">
                <SquareIcon className="size-3.5" />
              </button>
            ) : (
              <button type="button" onClick={send} title="发送"
                className="flex size-8 items-center justify-center rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90">
                <ArrowUpIcon className="size-4" />
              </button>
            )}
          </div>
        </footer>
      </main>
    </div>
  );
}
