"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { API_BASE, Citation, api } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { useLocale } from "@/components/Providers";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  citationMode?: string;
  pending?: boolean;
}

export default function ConversationPage() {
  const { t } = useLocale();
  const { conversationId } = useParams<{ conversationId: string }>();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [toolPhase, setToolPhase] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void api<any>(`/api/conversations/${conversationId}/`).then((c) =>
      setMessages(
        (c.messages ?? []).map((m: any) => ({
          role: m.role, content: m.content, citations: m.citations,
          citationMode: m.citation_mode,
        })),
      ),
    );
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, toolPhase]);

  async function send() {
    const content = input.trim();
    if (!content || streaming) return;

    setInput("");
    setStreaming(true);
    setMessages((m) => [...m, { role: "user", content },
                              { role: "assistant", content: "", pending: true }]);

    await streamChat(conversationId, content, {
      onToken: (token) =>
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + token };
          return next;
        }),
      // Tool turns stream lumpily by nature, so they render as a phase chip
      // rather than a stuttering token feed.
      onToolStart: (name) =>
        setToolPhase(name === "search_documents" ? t("searching") : name),
      onToolEnd: () => setToolPhase(null),
      onDone: (payload) => {
        setToolPhase(null);
        setMessages((m) => {
          const next = [...m];
          next[next.length - 1] = {
            role: "assistant", content: payload.content,
            citations: payload.citations, citationMode: payload.citation_mode,
          };
          return next;
        });
      },
      onError: (detail) => {
        setToolPhase(null);
        setMessages((m) => {
          const next = [...m];
          next[next.length - 1] = { role: "assistant", content: `⚠ ${detail}` };
          return next;
        });
      },
    });

    setStreaming(false);
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto pb-4">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${m.role === "user" ? "text-white" : "card"}`}
              style={m.role === "user" ? { background: "var(--accent)" } : undefined}
            >
              <p className="doc-text text-sm" dir="auto">
                {m.content || (m.pending ? "…" : "")}
              </p>

              {m.citations && m.citations.length > 0 && (
                <div className="mt-3 border-t pt-2" style={{ borderColor: "var(--border)" }}>
                  <p className="mb-1 text-xs" style={{ color: "var(--muted)" }}>
                    {t("sources")}
                    {m.citationMode === "implicit" && " (consulted)"}
                  </p>
                  <ul className="space-y-1">
                    {m.citations.map((c) => (
                      <li key={c.chunk_id} className="text-xs">
                        <a
                          href={`${API_BASE}/api/documents/${c.document_id}/pages/${c.page_start ?? 1}/image/`}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          <span className="numeric">[{c.n}]</span>{" "}
                          <span className="doc-text" dir="auto">{c.title}</span>
                          {c.page_start != null && (
                            <span className="numeric"> · p{c.page_start}</span>
                          )}
                        </a>
                        <p className="doc-text mt-0.5 line-clamp-2" dir="auto"
                           style={{ color: "var(--muted)" }}>
                          {c.quote}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        ))}

        {toolPhase && (
          <div className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
            {toolPhase}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2 border-t pt-3" style={{ borderColor: "var(--border)" }}>
        <textarea
          dir="auto"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
          }}
          rows={2}
          placeholder={t("askAnything")}
          className="doc-text flex-1 rounded-lg border p-2.5 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--bg)", color: "var(--fg)" }}
        />
        <button
          onClick={send}
          disabled={streaming || !input.trim()}
          className="self-end rounded-lg px-4 py-2.5 text-sm text-white disabled:opacity-40"
          style={{ background: "var(--accent)" }}
        >
          {t("send")}
        </button>
      </div>
    </div>
  );
}
