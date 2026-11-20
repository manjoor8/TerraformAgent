import { useEffect, useRef, useState } from "react";
import { Download, Minus, Plus, RotateCcw } from "lucide-react";
import { Spinner } from "./UI";

let mermaidInitialized = false;
let counter = 0;

async function renderMermaid(code: string): Promise<string> {
  const module = await import("mermaid");
  const mermaid = module.default;
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "dark",
      fontFamily: "Inter, sans-serif",
      themeVariables: {
        background: "#0d1624",
        primaryColor: "#0c4a6e",
        primaryTextColor: "#e0f2fe",
        primaryBorderColor: "#38bdf8",
        secondaryColor: "#14532d",
        tertiaryColor: "#3b0764",
        lineColor: "#64748b",
        textColor: "#cbd5e1",
        edgeLabelBackground: "#0d1624",
        clusterBkg: "#0b1420",
        clusterBorder: "#1e3048",
      },
      flowchart: { curve: "basis", padding: 14, nodeSpacing: 24, rankSpacing: 34, htmlLabels: true },
    });
    mermaidInitialized = true;
  }
  const id = `ta-mermaid-${Date.now()}-${counter++}`;
  const { svg } = await mermaid.render(id, code);
  return svg;
}

export function MermaidDiagram({ code, height = 460 }: { code: string; height?: number }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    renderMermaid(code)
      .then((result) => {
        if (!cancelled) setSvg(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  const download = () => {
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "architecture.svg";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-[#0a1320]">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wider text-faint">
          Architecture · interactive
        </span>
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost btn-sm" onClick={() => setScale((s) => Math.max(0.4, s - 0.15))} aria-label="Zoom out">
            <Minus size={14} />
          </button>
          <span className="w-10 text-center text-[11px] text-muted">{Math.round(scale * 100)}%</span>
          <button className="btn btn-ghost btn-sm" onClick={() => setScale((s) => Math.min(2.5, s + 0.15))} aria-label="Zoom in">
            <Plus size={14} />
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setScale(1)} aria-label="Reset zoom">
            <RotateCcw size={13} />
          </button>
          <button className="btn btn-ghost btn-sm" onClick={download} disabled={!svg} aria-label="Download SVG">
            <Download size={14} />
          </button>
        </div>
      </div>
      <div ref={containerRef} className="overflow-auto p-4" style={{ maxHeight: height }}>
        {error ? (
          <div className="p-4 text-sm text-rose-300">
            <p className="font-medium">Diagram could not be rendered</p>
            <p className="mt-1 text-xs text-muted">{error}</p>
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-black/40 p-3 text-xs text-slate-300">{code}</pre>
          </div>
        ) : svg ? (
          <div
            className="mermaid block w-full origin-top-left transition-transform"
            style={{ transform: `scale(${scale})` }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted">
            <Spinner /> Rendering diagram…
          </div>
        )}
      </div>
    </div>
  );
}
