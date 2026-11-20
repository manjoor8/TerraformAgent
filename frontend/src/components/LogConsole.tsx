import { useEffect, useRef, useState } from "react";
import { Check, Copy, TerminalSquare } from "lucide-react";

export function LogConsole({
  lines,
  title = "Execution log",
  empty = "No output yet.",
  live = false,
  maxHeight = 420,
}: {
  lines: string[];
  title?: string;
  empty?: string;
  live?: boolean;
  maxHeight?: number;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines.length]);

  const copy = async () => {
    await navigator.clipboard.writeText(lines.join("\n"));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-[#050a12]">
      <div className="flex items-center justify-between border-b border-line bg-surface/60 px-3 py-1.5">
        <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-faint">
          <TerminalSquare size={13} />
          {title}
          {live && (
            <span className="flex items-center gap-1 text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 pulse-dot" /> live
            </span>
          )}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={copy} disabled={!lines.length}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="overflow-auto p-3" style={{ maxHeight }}>
        {lines.length === 0 ? (
          <p className="py-6 text-center text-xs text-faint">{empty}</p>
        ) : (
          <pre className="log-line text-slate-300">
            {lines.map((line, index) => (
              <div key={index} className={line.toLowerCase().includes("error") ? "text-rose-300" : undefined}>
                {line}
              </div>
            ))}
          </pre>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
