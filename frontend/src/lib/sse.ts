import { API_BASE, ChatMode, ResearchMode, authHeaders, notifyUnauthorized } from "./api";

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
  // withCredentials sends the session cookie; EventSource cannot set headers.
  const source = new EventSource(`${API_BASE}/api/documents/${documentId}/events/`, {
    withCredentials: true,
  });

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
  /** The text streamed so far was a tool-calling turn, not the answer: clear it. */
  onReset?: () => void;
  onToolStart?: (name: string, args: unknown, phase?: string) => void;
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
  researchMode: ResearchMode,
  chatMode: ChatMode,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const send = async (forceCsrf: boolean) =>
    fetch(`${API_BASE}/api/conversations/${conversationId}/stream/`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(await authHeaders("POST", forceCsrf)),
      },
      body: JSON.stringify({ content, research_mode: researchMode, chat_mode: chatMode }),
      signal,
    });

  let res = await send(false);
  // Stale CSRF token (rotated by a login elsewhere): refresh once and retry.
  if (res.status === 403) res = await send(true);

  if (res.status === 401) {
    notifyUnauthorized();
    handlers.onError?.("session expired");
    return;
  }
  if (!res.ok || !res.body) {
    let detail = `stream failed: ${res.status}`;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    handlers.onError?.(detail);
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
        case "reset": handlers.onReset?.(); break;
        case "tool_start": handlers.onToolStart?.(payload.name, payload.args, payload.phase); break;
        case "tool_end": handlers.onToolEnd?.(payload.name, payload.hits); break;
        case "done": handlers.onDone?.(payload); break;
        case "error": handlers.onError?.(payload.detail); break;
      }
    }
  }
}
