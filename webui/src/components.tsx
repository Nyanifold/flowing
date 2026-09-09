import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Md({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/** JSON 语法高亮（key/字符串/数字/关键字四色；先 pretty-print）。 */
export function JsonView({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const html = text
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!)
    .replace(
      /(&quot;(?:\\.|[^&])*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g,
      (m, str, colon) => {
        if (str) return colon ? `<span class="j-key">${str}</span>${colon}` : `<span class="j-str">${str}</span>`;
        if (/^(true|false|null)$/.test(m)) return `<span class="j-kw">${m}</span>`;
        return `<span class="j-num">${m}</span>`;
      },
    );
  return (
    <pre className="mono m-0 overflow-x-auto text-[11px] leading-relaxed text-[#292929]"
      dangerouslySetInnerHTML={{ __html: html }} />
  );
}

/** 可折叠卡：思考 / 工具调用 / 工具返回 / 分叉分支共用形态（kimi 折叠卡样式）。 */
export function FoldCard({
  tone = "plain", title, sub, defaultOpen = false, children,
}: {
  tone?: "plain" | "thinking";
  title: string;
  sub?: string;
  defaultOpen?: boolean;
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`max-w-[85%] rounded-lg border bg-white/60 ${
      tone === "thinking" ? "border-dashed border-[#e8d9a8] bg-[#fffdf5]" : "border-[var(--line)]"}`}>
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left hover:bg-[var(--panel2)]">
        <span className="w-3 text-[10px] text-[var(--faint)]">{open ? "▾" : "▸"}</span>
        <span className={`mono text-[11px] ${tone === "thinking" ? "text-[#8a6d1a]" : "text-[var(--muted)]"}`}>
          {title}
        </span>
        {sub && !open && (
          <span className="mono max-w-[36em] overflow-hidden text-[10px] text-ellipsis whitespace-nowrap text-[var(--faint)]">
            {sub}
          </span>
        )}
      </button>
      {open && <div className="border-t border-[var(--line2)] px-3 py-2">{children}</div>}
    </div>
  );
}
