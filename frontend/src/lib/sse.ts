import {
  API_BASE, ChatMode, CodeRun, GeneratedFile, ResearchMode, ThinkingMode, authHeaders, notifyUnauthorized,
} from "./api";

/**
 * Subscribe to a document's lifecycle + OCR progress stream.
 *
 * Native EventSource handles reconnection and Last-Event-ID for us, which
 * matters because a multi-engine OCR batch runs for minutes and the user will
 * switch tabs. But it gives up for good on any non-200 answer (a 502 while the
 * backend restarts, a 401 once the session expires), so a closed source is
 * reopened here with backoff. Each new connection starts with a `snapshot`.
 */
export function subscribeToDocument(
  documentId: string,
  handlers: Record<string, (data: any) => void>,
): () => void {
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  let closed = false;

  const open = () => {
    // withCredentials sends the session cookie; EventSource cannot set headers.
    const current = new EventSource(`${API_BASE}/api/documents/${documentId}/events/`, {
      withCredentials: true,
    });
    source = current;
    for (const [event, handler] of Object.entries(handlers)) {
      current.addEventListener(event, (e) => {
        if (event === "snapshot") attempt = 0; // connected: reset the backoff
        try {
          handler(JSON.parse((e as MessageEvent).data));
        } catch {
          /* heartbeat, malformed frame, or a connection error without data */
        }
      });
    }
    current.addEventListener("error", () => {
      // CONNECTING means the browser is already retrying on its own.
      if (closed || current.readyState !== EventSource.CLOSED) return;
      const delay = Math.min(30_000, 1000 * 2 ** attempt);
      attempt += 1;
      retryTimer = setTimeout(open, delay);
    });
  };
  open();

  return () => {
    closed = true;
    clearTimeout(retryTimer);
    source?.close();
  };
}

export interface StreamHandlers {
  onToken?: (t: string) => void;
  /** The text streamed so far was a tool-calling turn, not the answer: clear it. */
  onReset?: () => void;
  /** Model reasoning (or deep-think working notes), streamed separately from the answer. */
  onThinking?: (t: string) => void;
  /** Deep think / deep research progress. */
  onPhase?: (phase: PhaseEvent) => void;
  onToolStart?: (name: string, args: unknown, phase?: string) => void;
  onToolEnd?: (name: string, hits: number) => void;
  /** General mode ran code: its output and the files it saved. */
  onCodeRun?: (run: CodeRunEvent) => void;
  onDone?: (payload: any) => void;
  onError?: (detail: string) => void;
}

/**
 * Stream a chat turn.
 *
 * Native EventSource cannot POST, so this parses the SSE frames off a fetch
 * ReadableStream by hand.
 */
export interface PhaseEvent {
  phase: string;
  detail?: string;
  index?: number;
  total?: number;
  steps?: string[];
  answered?: number;
  sources?: number;
  queries?: number;
}

export interface CodeRunEvent extends CodeRun {
  files: GeneratedFile[];
}

export interface TurnOptions {
  researchMode: ResearchMode;
  chatMode: ChatMode;
  thinking: ThinkingMode;
  attachmentIds: string[];
}

export async function streamChat(
  conversationId: string,
  content: string,
  options: TurnOptions,
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
      body: JSON.stringify({
        content,
        research_mode: options.researchMode,
        chat_mode: options.chatMode,
        thinking: options.thinking,
        attachment_ids: options.attachmentIds,
      }),
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

  const dispatch = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      // lines starting with ':' are heartbeat comments
    }
    if (!dataLines.length) return;

    let payload: any;
    try {
      payload = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }

    switch (event) {
      case "token": handlers.onToken?.(payload.t); break;
      case "reset": handlers.onReset?.(); break;
      case "thinking": handlers.onThinking?.(payload.t); break;
      case "phase": handlers.onPhase?.(payload); break;
      case "tool_start": handlers.onToolStart?.(payload.name, payload.args, payload.phase); break;
      case "tool_end": handlers.onToolEnd?.(payload.name, payload.hits); break;
      case "code_run": handlers.onCodeRun?.(payload); break;
      case "done": handlers.onDone?.(payload); break;
      case "error": handlers.onError?.(payload.detail); break;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) dispatch(frame);
  }
  // A last frame the server closed without its trailing blank line.
  buffer += decoder.decode();
  if (buffer.trim()) dispatch(buffer);
}
