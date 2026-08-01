import { API_BASE } from "./api";

/**
 * Subscribe to a document's lifecycle + OCR progress stream.
 *
 * Native EventSource handles reconnection and Last-Event-ID for us, which
 * matters because a multi-engine OCR batch runs for minutes and the user will
 * switch tabs.
 */
export function subscribeToDocument(
  documentId: string,
  handlers: Record<string, (data: any) => void>,
): () => void {
  const source = new EventSource(`${API_BASE}/api/documents/${documentId}/events/`);

  const registered: Array<[string, EventListener]> = [];
  for (const [event, handler] of Object.entries(handlers)) {
    const listener: EventListener = (e) => {
      try {
        handler(JSON.parse((e as MessageEvent).data));
      } catch {
        /* heartbeat or malformed frame */
      }
    };
    source.addEventListener(event, listener);
    registered.push([event, listener]);
  }

  return () => {
    for (const [event, listener] of registered) source.removeEventListener(event, listener);
    source.close();
  };
}

export interface StreamHandlers {
  onToken?: (t: string) => void;
  onToolStart?: (name: string, args: unknown) => void;
  onToolEnd?: (name: string, hits: number) => void;
  onDone?: (payload: any) => void;
  onError?: (detail: string) => void;
}

/**
 * Stream a chat turn.
 *
 * Native EventSource cannot POST, so this parses the SSE frames off a fetch
 * ReadableStream by hand.
 */
export async function streamChat(
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/stream/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });

  if (!res.ok || !res.body) {
    handlers.onError?.(`stream failed: ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
        // lines starting with ':' are heartbeat comments
      }
      if (!dataLines.length) continue;

      let payload: any;
      try {
        payload = JSON.parse(dataLines.join("\n"));
      } catch {
        continue;
      }

      switch (event) {
        case "token": handlers.onToken?.(payload.t); break;
        case "tool_start": handlers.onToolStart?.(payload.name, payload.args); break;
        case "tool_end": handlers.onToolEnd?.(payload.name, payload.hits); break;
        case "done": handlers.onDone?.(payload); break;
        case "error": handlers.onError?.(payload.detail); break;
      }
    }
  }
}
