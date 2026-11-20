import Editor from "@monaco-editor/react";
import { Spinner } from "./UI";

export function CodeEditor({
  value,
  onChange,
  language = "plaintext",
  readOnly = false,
  height = "100%",
}: {
  value: string;
  onChange?: (value: string) => void;
  language?: string;
  readOnly?: boolean;
  height?: string | number;
}) {
  return (
    <Editor
      height={height}
      language={language}
      theme="vs-dark"
      value={value}
      onChange={(next) => onChange?.(next ?? "")}
      loading={
        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
          <Spinner /> Loading editor…
        </div>
      }
      options={{
        readOnly,
        minimap: { enabled: false },
        fontSize: 13,
        fontFamily: "JetBrains Mono, monospace",
        lineHeight: 21,
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        renderLineHighlight: "line",
        padding: { top: 12, bottom: 12 },
        tabSize: 2,
        wordWrap: "on",
        automaticLayout: true,
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
      }}
    />
  );
}
