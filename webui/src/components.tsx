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
export function JsonBlock({ label, value }: { label?: string; value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const html = text
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!)
    .replace(
      /(&quot;(?:\\.|[^&])*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g,
      (m, str, colon) => {
        if (str) return colon ? `<span class="j-key">${str}</span>${colon}` : `<span class="j-str">${str}</span>`;
        if (/^(true|false|null)$/.test(m)) return `<span class="j-kw">${m}</span>`;
        return `<span class="j-num">${m}</span>`;
      });
  return (
    <div>
      {label && <div className="mono mb-1 text-[10px] uppercase tracking-wide text-[var(--faint)]">{label}</div>}
      <pre className="mono m-0 overflow-x-auto rounded-md border border-[var(--border)] bg-[var(--muted)] p-2 text-[11px] leading-relaxed"
        dangerouslySetInnerHTML={{ __html: html }} />
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
