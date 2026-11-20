/** Server-sent events over fetch so the bearer token stays in a header, not the URL. */
import { API_BASE, authHeaders } from "./api";

export interface SSEEvent {
  type: string;
  data: unknown;
  id?: string;
}

export function subscribeProjectEvents(
  projectId: string,
  onEvent: (event: SSEEvent) => void,
  options: { since?: number; onError?: (message: string) => void } = {},
): () => void {
  let disposed = false;
  let controller: AbortController | null = null;
  let retryTimer: number | null = null;

  const connect = async () => {
    if (disposed) return;
    controller = new AbortController();
    try {
      const response = await fetch(
        `${API_BASE}/projects/${projectId}/events?since=${options.since ?? 0}`,
        { headers: authHeaders(), signal: controller.signal, cache: "no-store" },
      );
      if (!response.ok || !response.body) {
        throw new Error(`Event stream failed (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          parseBlock(block, onEvent);
          boundary = buffer.indexOf("\n\n");
        }
      }
      if (!disposed) throw new Error("Event stream closed");
    } catch (error) {
      if (disposed || (error instanceof DOMException && error.name === "AbortError")) return;
      options.onError?.(error instanceof Error ? error.message : String(error));
      retryTimer = window.setTimeout(connect, 3000);
    }
  };

  void connect();

  return () => {
    disposed = true;
    controller?.abort();
    if (retryTimer) window.clearTimeout(retryTimer);
  };
}

function parseBlock(block: string, onEvent: (event: SSEEvent) => void) {
  const lines = block.split("\n");
  let type = "message";
  let id: string | undefined;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return;
  try {
    onEvent({ type, data: JSON.parse(dataLines.join("\n")), id });
  } catch {
    onEvent({ type, data: dataLines.join("\n"), id });
  }
}
