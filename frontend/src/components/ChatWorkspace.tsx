"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  API_BASE,
  Citation,
  ConversationDetail,
  ResearchMode,
  SelectedDocument,
  api,
  createConversation,
  updateConversationScope,
} from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { useLocale } from "./Providers";
import { Markdown } from "./Markdown";
import { ChatSidebar } from "./ChatSidebar";
import { ChatComposer } from "./ChatComposer";

const SIDEBAR_KEY = "ocr-rag-chat-sidebar-collapsed";
const pendingKey = (conversationId: string) => `ocr-rag-pending-chat:${conversationId}`;

interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  citationMode?: string;
  pending?: boolean;
}

interface PendingTurn {
  content: string;
  researchMode: ResearchMode;
}

export function ChatWorkspace({ conversationId }: { conversationId?: string }) {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [selectedSources, setSelectedSources] = useState<SelectedDocument[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [toolPhase, setToolPhase] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const hydratedIdRef = useRef<string | null>(null);
  const pendingStartedRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api<ConversationDetail>(`/api/conversations/${conversationId}/`),
    enabled: Boolean(conversationId),
  });

  useEffect(() => {
    setCollapsed(localStorage.getItem(SIDEBAR_KEY) === "true");
  }, []);

  useEffect(() => {
    hydratedIdRef.current = null;
    pendingStartedRef.current = null;
    setMessages([]);
    setSelectedSources([]);
    setToolPhase(null);
    setSourceError(null);
  }, [conversationId]);

  useEffect(() => {
    const conversation = conversationQuery.data;
    if (!conversationId || !conversation || hydratedIdRef.current === conversationId) return;

    hydratedIdRef.current = conversationId;
    setMessages(
      conversation.messages
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) => ({
          role: message.role as "user" | "assistant",
          content: message.content,
          citations: message.citations,
          citationMode: message.citation_mode,
        })),
    );
    setSelectedSources(conversation.selected_documents ?? []);

    const stored = sessionStorage.getItem(pendingKey(conversationId));
    if (!stored || pendingStartedRef.current === conversationId) return;
    sessionStorage.removeItem(pendingKey(conversationId));
    pendingStartedRef.current = conversationId;
    try {
      const pending = JSON.parse(stored) as PendingTurn;
      if (pending.content && ["fast", "balanced", "deep"].includes(pending.researchMode)) {
        void sendTurn(conversationId, pending.content, pending.researchMode);
      }
    } catch {
      setSourceError(t("pendingMessageFailed"));
    }
    // sendTurn intentionally reads only stable setters/refs; adding it here
    // would re-run the one-time session handoff on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, conversationQuery.data, t]);

  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, toolPhase]);

  function toggleSidebar() {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(SIDEBAR_KEY, String(next));
  }

  function onScroll() {
    const element = scrollRef.current;
    if (!element) return;
    pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
  }

  async function changeSources(next: SelectedDocument[]) {
    if (streaming) return;
    const previous = selectedSources;
    setSelectedSources(next);
    setSourceError(null);

    if (!conversationId) return;
    try {
      const updated = await updateConversationScope(
        conversationId,
        next.map((document) => document.id),
      );
      setSelectedSources(updated.selected_documents ?? next);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    } catch (error) {
      setSelectedSources(previous);
      setSourceError(error instanceof Error ? error.message : t("scopeUpdateFailed"));
    }
  }

  async function submit(content: string, researchMode: ResearchMode) {
    setSourceError(null);
    if (conversationId) {
      await sendTurn(conversationId, content, researchMode);
      return;
    }

    try {
      const created = await createConversation(
        selectedSources.length ? "selected" : "all",
        selectedSources.map((document) => document.id),
      );
      sessionStorage.setItem(
        pendingKey(created.id),
        JSON.stringify({ content, researchMode } satisfies PendingTurn),
      );
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      router.push(`/chat/${created.id}`);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : t("conversationCreateFailed"));
    }
  }

  async function sendTurn(id: string, content: string, researchMode: ResearchMode) {
    if (streaming) return;
    setStreaming(true);
    setToolPhase(null);
    pinnedRef.current = true;
    setMessages((current) => [
      ...current,
      { role: "user", content },
      { role: "assistant", content: "", pending: true },
    ]);

    const fail = (detail: string) => {
      setToolPhase(null);
      setMessages((current) => {
        const next = [...current];
        next[next.length - 1] = { role: "assistant", content: `⚠ ${detail}` };
        return next;
      });
    };

    try {
      await streamChat(id, content, researchMode, {
        onToken: (token) =>
          setMessages((current) => {
            const next = [...current];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              content: last.content + token,
              pending: false,
            };
            return next;
          }),
        onToolStart: (_name, _args, phase) => setToolPhase(phaseLabel(phase)),
        onToolEnd: () => setToolPhase(null),
        onDone: (payload) => {
          setToolPhase(null);
          setMessages((current) => {
            const next = [...current];
            next[next.length - 1] = {
              role: "assistant",
              content: payload.content,
              citations: payload.citations,
              citationMode: payload.citation_mode,
            };
            return next;
          });
        },
        onError: fail,
      });
    } catch (error) {
      fail(error instanceof Error ? error.message : t("streamFailed"));
    } finally {
      setStreaming(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }

  function phaseLabel(phase?: string) {
    if (phase === "comparing") return t("comparingSources");
    if (phase === "verifying") return t("verifyingEvidence");
    if (phase === "reviewing") return t("reviewingSources");
    return t("searching");
  }

  const showEmptyState = !conversationId || (
    !conversationQuery.isLoading && messages.length === 0 && !streaming
  );

  return (
    <div className="flex h-dvh min-w-0 overflow-hidden" style={{ background: "var(--bg)" }}>
      <ChatSidebar
        activeId={conversationId}
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onCollapse={toggleSidebar}
        onMobileClose={() => setMobileOpen(false)}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b px-3 sm:px-4"
                style={{ borderColor: "var(--border)" }}>
          <button
            type="button"
            onClick={() => collapsed ? toggleSidebar() : setMobileOpen(true)}
            className={`grid h-9 w-9 place-items-center rounded-xl hover:bg-[var(--hover)] ${
              collapsed ? "" : "lg:hidden"
            }`}
            aria-label={t("showSidebar")}
            title={t("showSidebar")}
          >
            ☰
          </button>
          <p className="doc-text min-w-0 truncate text-sm font-medium" dir="auto">
            {conversationQuery.data?.title || t("newChat")}
          </p>
          <span className="ms-auto rounded-full px-2.5 py-1 text-xs"
                style={{ background: "var(--hover)", color: "var(--muted)" }}>
            {selectedSources.length
              ? t("selectedSourceCount").replace("{count}", String(selectedSources.length))
              : t("wholeLibrary")}
          </span>
        </header>

        {conversationQuery.isLoading && conversationId ? (
          <div className="grid flex-1 place-items-center text-sm" style={{ color: "var(--muted)" }}>
            {t("loading")}
          </div>
        ) : showEmptyState ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col items-center justify-center px-4 py-10">
              <div className="mb-7 text-center">
                <div className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-2xl text-xl text-white"
                     style={{ background: "var(--accent)" }} aria-hidden>
                  ◈
                </div>
                <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                  {t("chatWelcome")}
                </h1>
                <p className="mt-2 text-sm sm:text-base" style={{ color: "var(--muted)" }}>
                  {t("chatWelcomeHint")}
                </p>
              </div>
              <ChatComposer
                selectedSources={selectedSources}
                streaming={streaming}
                sourceError={sourceError}
                onSourcesChange={changeSources}
                onSend={submit}
              />
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
              <div className="mx-auto w-full max-w-3xl space-y-7 px-4 py-7 sm:px-6">
                {messages.map((message, index) => (
                  <div key={index} className={message.role === "user" ? "flex justify-end" : ""}>
                    {message.role === "user" ? (
                      <div className="max-w-[85%] rounded-2xl rounded-ee-md px-4 py-2.5"
                           style={{ background: "var(--user-bubble)" }}>
                        <Markdown>{message.content}</Markdown>
                      </div>
                    ) : (
                      <article className="min-w-0">
                        {message.content ? <Markdown>{message.content}</Markdown> : null}
                        {message.pending && <span className="md-cursor" aria-hidden />}
                        {message.citations && message.citations.length > 0 && (
                          <div className="mt-4">
                            <p className="mb-2 text-xs font-medium" style={{ color: "var(--muted)" }}>
                              {t("sources")}
                              {message.citationMode === "implicit" ? ` · ${t("consulted")}` : ""}
                            </p>
                            <div className="grid gap-2 sm:grid-cols-2">
                              {message.citations.map((citation) => (
                                <a
                                  key={citation.chunk_id}
                                  href={`${API_BASE}/api/documents/${citation.document_id}/pages/${citation.page_start ?? 1}/image/`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="group rounded-xl border p-3 transition hover:bg-[var(--hover)]"
                                  style={{ borderColor: "var(--border)" }}
                                >
                                  <p className="flex items-center gap-2 text-xs font-medium">
                                    <span className="numeric" style={{ color: "var(--accent)" }}>
                                      [{citation.n}]
                                    </span>
                                    <span className="doc-text min-w-0 flex-1 truncate" dir="auto">
                                      {citation.title}
                                    </span>
                                    {citation.page_start != null && (
                                      <span className="numeric" style={{ color: "var(--muted)" }}>
                                        {t("pageShort")}{citation.page_start}
                                      </span>
                                    )}
                                  </p>
                                  <p className="doc-text mt-1.5 line-clamp-2 text-xs" dir="auto"
                                     style={{ color: "var(--muted)" }}>
                                    {citation.quote}
                                  </p>
                                </a>
                              ))}
                            </div>
                          </div>
                        )}
                      </article>
                    )}
                  </div>
                ))}

                {toolPhase && (
                  <div className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
                    <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    {toolPhase}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="chat-composer-dock shrink-0 px-4 pb-3 pt-2 sm:px-6">
              <div className="mx-auto w-full max-w-3xl">
                <ChatComposer
                  selectedSources={selectedSources}
                  streaming={streaming}
                  sourceError={sourceError}
                  onSourcesChange={changeSources}
                  onSend={submit}
                />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
