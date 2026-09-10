// 组件家族的组织方式与样式直接对应 kimi web 的组件（~/agents/kimi-cli/web）：
// components/ai-elements（Reasoning / Tool / CodeBlock / Message /
// Conversation）与 features/chat/components（assistant-message 的流式呼吸点、
// chat-prompt-composer 输入条、sessions 侧栏行）。仅样式与组织方式——数据与
// 逻辑全部走 flowing 的 serve HTTP API。
import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BrainIcon,
  CheckIcon,
  ChevronRightIcon,
  Loader2Icon,
  WrenchIcon,
  XIcon,
} from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ---- Message（对应 ai-elements 的 Message / MessageContent / MessageResponse）--
export function MessageResponse({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className="flex items-start gap-2 w-full max-w-full text-sm leading-relaxed">
      <span className="relative mt-1.5 size-2 shrink-0">
        <span className={cn("absolute inset-0 rounded-full transition-all",
          streaming ? "streaming-dot" : "bg-[var(--muted-foreground)]/40")} />
      </span>
      <div className="min-w-0 flex-1">
        <Md text={text} />
      </div>
    </div>
  );
}

export function Md({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

// ---- Reasoning（思考折叠；流式期间自动展开、结束后自动收起——对齐 kimi
// ai-elements/reasoning 的 auto-close 行为） ------------------------------------
export function Reasoning({ text, streaming }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(Boolean(streaming));
  const [start, setStart] = useState<number | null>(streaming ? Date.now() : null);
  const [duration, setDuration] = useState<number | null>(null);
  const autoClosed = useRef(false);

  useEffect(() => {
    if (streaming && start === null) setStart(Date.now());
    if (!streaming && start !== null) {
      setDuration(Math.ceil((Date.now() - start) / 1000));
      setStart(null);
    }
  }, [streaming, start]);

  useEffect(() => {   // 流式结束 1s 后自动收起（一次）
    if (!streaming && open && !autoClosed.current) {
      const t = setTimeout(() => { setOpen(false); autoClosed.current = true; }, 1000);
      return () => clearTimeout(t);
    }
  }, [streaming, open]);

  return (
    <div className="not-prose mb-2">
      <button type="button" onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-sm text-[var(--muted-foreground)]">
        <BrainIcon className="size-4" />
        <span className={cn(streaming && "text-[var(--foreground)]/70 italic")}>
          {streaming ? "Thinking..." : duration != null ? `Thought for ${duration} seconds` : "Thought process"}
        </span>
        <ChevronRightIcon className={cn("size-4 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-1 border-l-2 border-[var(--border)] pl-3 text-[12.5px] whitespace-pre-wrap text-[var(--muted-foreground)]">
          {text}
        </div>
      )}
    </div>
  );
}

// ---- CodeBlock + JSON 高亮 -----------------------------------------------------
// 线性状态机扫描（无回溯）：历史教训——正则「懒惰量词 + 可选组」在真实工具
// 返回（长字符串、嵌套 JSON、大量 & 实体）上病态回溯，页面直接卡死
function escHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function highlightJson(text: string): string {
  const out: string[] = [];
  const n = text.length;
  let i = 0;
  while (i < n) {
    const c = text[i];
    if (c === '"') {   // 字符串：扫描到未转义的闭引号
      let j = i + 1;
      while (j < n) {
        if (text[j] === "\\") { j += 2; continue; }
        if (text[j] === '"') { j++; break; }
        j++;
      }
      // key 判定：跳过空白看下一字符是否 ':'
      let k = j;
      while (text[k] === " " || text[k] === "\n" || text[k] === "\t" || text[k] === "\r") k++;
      const cls = text[k] === ":" ? "j-key" : "j-str";
      out.push(`<span class="${cls}">${escHtml(text.slice(i, j))}</span>`);
      i = j;
      continue;
    }
    const rest = text.slice(i, i + 16);   // 数字/关键字着色（短窗，线性）
    const m = /^(true|false|null|-?\d+(?:\.\d+)?)/.exec(rest);
    if (m && (i === 0 || /[\s,[\]{:]/.test(text[i - 1]))) {
      const cls = m[1] === "true" || m[1] === "false" || m[1] === "null" ? "j-kw" : "j-num";
      out.push(`<span class="${cls}">${m[1]}</span>`);
      i += m[1].length;
      continue;
    }
    out.push(escHtml(c));
    i++;
  }
  return out.join("");
}

export function JsonBlock({ label, value }: { label?: string; value: unknown }) {
  const raw = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const MAX = 200_000;   // 超大工具返回截断展示（保 DOM 规模可控）
  const text = raw.length > MAX
    ? raw.slice(0, MAX) + `\n… (truncated, ${raw.length - MAX} more chars)`
    : raw;
  return (
    <div>
      {label && <div className="mono mb-1 text-[10px] uppercase tracking-wide text-[var(--faint)]">{label}</div>}
      <pre className="mono m-0 overflow-x-auto rounded-md border border-[var(--border)] bg-[var(--muted)] p-2 text-[11px] leading-relaxed"
        dangerouslySetInnerHTML={{ __html: highlightJson(text) }} />
    </div>
  );
}

// ---- FoldCard（通用折叠卡：分叉分支头 / meta 用；kimi 折叠卡形态）---------------
export function FoldCard({ title, sub, defaultOpen = false, children }: {
  title: string;
  sub?: string;
  defaultOpen?: boolean;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="max-w-[85%] rounded-lg border border-[var(--border)] bg-white/70">
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left hover:bg-[var(--accent)]">
        <ChevronRightIcon className={cn("size-3.5 text-[var(--muted-foreground)] transition-transform", open && "rotate-90")} />
        <span className="mono text-[11px] text-[var(--muted-foreground)]">{title}</span>
        {sub && !open && (
          <span className="mono max-w-[36em] truncate text-[10px] text-[var(--faint)]">{sub}</span>
        )}
      </button>
      {open && <div className="border-t border-[var(--border)] px-3 py-2">{children}</div>}
    </div>
  );
}

// ---- Tool（调用/返回折叠卡；header = 状态图标 + 名称 + 主参数内联） --------------
function primaryParam(input: unknown): string | null {
  // 对齐 kimi getPrimaryParam：优先 path/command/pattern/url/query，取首个
  // 字符串参数兜底，>50 截断
  if (!input || typeof input !== "object") return null;
  const rec = input as Record<string, unknown>;
  for (const key of ["path", "command", "pattern", "url", "query"]) {
    const v = rec[key];
    if (typeof v === "string" && v.length > 0) return v.length > 50 ? v.slice(0, 50) + "…" : v;
  }
  const first = Object.values(rec).find((v) => typeof v === "string");
  if (typeof first === "string") return first.length > 50 ? first.slice(0, 50) + "…" : first;
  return null;
}

export function ToolCard({ name, args, output, status }: {
  name: string;
  args?: unknown;
  output?: unknown;
  status?: string;   // completed / pending / blocked / error / streaming
}) {
  const [open, setOpen] = useState(false);
  const param = primaryParam(args);
  const icon =
    status === "completed" ? <CheckIcon className="size-3 text-[var(--success)]" /> :
    status === "error" ? <XIcon className="size-3 text-[var(--destructive)]" /> :
    <Loader2Icon className="size-3 animate-spin text-[var(--muted-foreground)]" />;
  return (
    <div className="not-prose mb-1 w-full text-sm">
      <button type="button" onClick={() => setOpen(!open)}
        className="group flex items-center gap-1.5">
        <WrenchIcon className="size-3.5 shrink-0 text-[var(--muted-foreground)]" />
        <span className="font-medium text-[var(--primary)]">{name}</span>
        {icon}
        {param && !open && (
          <span className="mono truncate text-[11px] text-[var(--muted-foreground)]">{param}</span>
        )}
        <ChevronRightIcon className={cn("size-4 text-[var(--muted-foreground)] transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-2 border-l-2 border-[var(--border)] pl-3">
          <JsonBlock label="Parameters" value={args ?? {}} />
          {output !== undefined &&
            <JsonBlock label={status === "error" ? "Error" : "Result"} value={output} />}
        </div>
      )}
    </div>
  );
}
