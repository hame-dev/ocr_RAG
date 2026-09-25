"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, BookOpenText, Check, Copy, FileText, Library, RotateCw, Sparkles,
} from "lucide-react";
import {
  API_BASE, ChatMode, Citation, ConversationDetail, ResearchMode, SelectedDocument, api,
  createConversation, updateConversationScope,
} from "@/lib/api";
import { fmt, num, StringKey } from "@/lib/i18n";
import { streamChat } from "@/lib/sse";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger, Hint } from "@/components/ui/tooltip";
import { BrandMark } from "./app-shell/AppSidebar";
import { ChatComposer } from "./ChatComposer";
import { Markdown } from "./Markdown";
import { useLocale } from "./Providers";

const pendingKey = (conversationId: string) => `ocr-rag-pending-chat:${conversationId}`;

interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  citationMode?: string;
  chatMode?: ChatMode;
  pending?: boolean;
  error?: string;
}

interface PendingTurn {
  content: string;
  researchMode: ResearchMode;
  chatMode: ChatMode;
}

const SUGGESTIONS: Record<ChatMode, StringKey[]> = {
  documents: ["suggestDoc1", "suggestDoc2", "suggestDoc3"],
  general: ["suggestGen1", "suggestGen2", "suggestGen3"],
};

export function ChatWorkspace({ conversationId }: { conversationId?: string }) {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [selectedSources, setSelectedSources] = useState<SelectedDocument[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [toolPhase, setToolPhase] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [composerMode, setComposerMode] = useState<ChatMode>("documents");
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null);
  const hydratedIdRef = useRef<string | null>(null);
  const pendingStartedRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const lastTurnRef = useRef<PendingTurn | null>(null);

  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api<ConversationDetail>(`/api/conversations/${conversationId}/`),
    enabled: Boolean(conversationId),
  });

  useEffect(() => {
    hydratedIdRef.current = null;
    pendingStartedRef.current = null;
    setMessages([]);
    setSelectedSources([]);
    setToolPhase(null);
    setSourceError(null);
    return () => abortRef.current?.abort();
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
          chatMode: message.chat_mode,
          error: message.error || undefined,
        })),
    );
    setSelectedSources(conversation.selected_documents ?? []);

    let stored: string | null = null;
    try { stored = sessionStorage.getItem(pendingKey(conversationId)); } catch {}
    if (!stored || pendingStartedRef.current === conversationId) return;
    try { sessionStorage.removeItem(pendingKey(conversationId)); } catch {}
    pendingStartedRef.current = conversationId;
    try {
      const pending = JSON.parse(stored) as PendingTurn;
      const chatMode: ChatMode = pending.chatMode === "general" ? "general" : "documents";
      if (pending.content && ["fast", "balanced", "deep"].includes(pending.researchMode)) {
        void sendTurn(conversationId, pending.content, pending.researchMode, chatMode);
      }
    } catch {
      setSourceError(t("pendingMessageFailed"));
    }
    // sendTurn reads only stable setters/refs; adding it here would re-run the
    // one-time session handoff on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, conversationQuery.data, t]);

  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, toolPhase]);

  function onScroll() {
    const element = scrollRef.current;
    if (!element) return;
    pinnedRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
  }

  async function changeSources(next: SelectedDocument[]) {
    if (streaming) return;
    const previous = selectedSources;
    setSelectedSources(next);
    setSourceError(null);
    if (!conversationId) return;
    try {
      const updated = await updateConversationScope(conversationId, next.map((document) => document.id));
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

  async function submit(content: string, researchMode: ResearchMode, chatMode: ChatMode) {
    setSourceError(null);
    if (conversationId) {
      await sendTurn(conversationId, content, researchMode, chatMode);
      return;
    }
    try {
      const created = await createConversation(
        selectedSources.length ? "selected" : "all",
        selectedSources.map((document) => document.id),
      );
      try {
        sessionStorage.setItem(
          pendingKey(created.id),
          JSON.stringify({ content, researchMode, chatMode } satisfies PendingTurn),
        );
      } catch {}
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      router.push(`/chat/${created.id}`);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : t("conversationCreateFailed"));
    }
  }

  function updateLast(update: (message: DisplayMessage) => DisplayMessage) {
    setMessages((current) => {
      const next = [...current];
      next[next.length - 1] = update(next[next.length - 1]);
      return next;
    });
  }

  async function sendTurn(id: string, content: string, researchMode: ResearchMode, chatMode: ChatMode) {
    if (streaming) return;
    lastTurnRef.current = { content, researchMode, chatMode };
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setToolPhase(null);
    pinnedRef.current = true;
    setMessages((current) => [
      ...current,
      { role: "user", content, chatMode },
      { role: "assistant", content: "", pending: true, chatMode },
    ]);

    const fail = (detail: string) => {
      setToolPhase(null);
      updateLast((last) => ({ ...last, pending: false, error: detail }));
    };

    try {
      await streamChat(id, content, researchMode, chatMode, {
        onToken: (token) => updateLast((last) => ({ ...last, content: last.content + token })),
        // The streamed text was a tool-calling turn ("let me search…"): drop it.
        onReset: () => updateLast((last) => ({ ...last, content: "" })),
        onToolStart: (_name, _args, phase) => setToolPhase(phaseLabel(phase)),
        onToolEnd: () => setToolPhase(null),
        onDone: (payload) => {
          setToolPhase(null);
          updateLast(() => ({
            role: "assistant",
            content: payload.content,
            citations: payload.citations,
            citationMode: payload.citation_mode,
            chatMode,
          }));
        },
        onError: fail,
      }, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) {
        setToolPhase(null);
        updateLast((last) => ({ ...last, pending: false }));
      } else {
        fail(error instanceof Error ? error.message : t("streamFailed"));
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      // Picks up the title the first turn assigned. Messages are hydrated once
      // per conversation, so this refetch never overwrites the live transcript.
      void queryClient.invalidateQueries({ queryKey: ["conversation", id] });
    }
  }

  function retryLast() {
    if (!conversationId) return;
    // Prefer the exact settings of the turn that failed in this session; for a
    // failure loaded from history, fall back to the last question as asked.
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    const turn = lastTurnRef.current ?? (lastUser && {
      content: lastUser.content,
      researchMode: "balanced" as ResearchMode,
      chatMode: lastUser.chatMode ?? composerMode,
    });
    if (turn) void sendTurn(conversationId, turn.content, turn.researchMode, turn.chatMode);
  }

  function phaseLabel(phase?: string) {
    if (phase === "comparing") return t("comparingSources");
    if (phase === "verifying") return t("verifyingEvidence");
    if (phase === "reviewing") return t("reviewingSources");
    return t("searching");
  }

  const loading = Boolean(conversationId) && conversationQuery.isPending;
  const showEmptyState = !conversationId || (!loading && messages.length === 0 && !streaming);
  const composer = (
    <ChatComposer
      selectedSources={selectedSources}
      streaming={streaming}
      sourceError={sourceError}
      onSourcesChange={changeSources}
      onSend={submit}
      onStop={() => abortRef.current?.abort()}
      draft={draft}
      onModeChange={setComposerMode}
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4 sm:px-6">
        <p className="min-w-0 flex-1 truncate text-sm font-medium" dir="auto">
          {conversationQuery.data?.title || t("newChat")}
        </p>
        <Badge variant="secondary" className="gap-1.5">
          {selectedSources.length ? <FileText /> : <Library />}
          {selectedSources.length
            ? fmt(t("selectedSourceCount"), { count: selectedSources.length })
            : t("wholeLibrary")}
        </Badge>
      </header>

      {loading ? (
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8">
          <Skeleton className="ms-auto h-10 w-2/3 rounded-2xl" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="ms-auto h-10 w-1/2 rounded-2xl" />
        </div>
      ) : showEmptyState ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center px-4 py-10">
            <div className="mb-8 text-center">
              <BrandMark className="mx-auto mb-5 size-11 rounded-xl" />
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t("chatWelcome")}</h1>
              <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{t("chatWelcomeHint")}</p>
            </div>
            {composer}
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS[composerMode].map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setDraft({ text: t(key), nonce: Date.now() })}
                  className="rounded-full border bg-card px-3.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  {t(key)}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div ref={scrollRef} onScroll={onScroll} className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8 sm:px-6">
              {messages.map((message, index) =>
                message.role === "user" ? (
                  <div key={index} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-ee-md bg-muted px-4 py-2.5">
                      <Markdown>{message.content}</Markdown>
                    </div>
                  </div>
                ) : (
                  <AssistantMessage
                    key={index}
                    message={message}
                    toolPhase={index === messages.length - 1 ? toolPhase : null}
                    onRetry={index === messages.length - 1 && !streaming ? retryLast : undefined}
                  />
                ),
              )}
              <div ref={bottomRef} />
            </div>
          </div>
          <div className="shrink-0 bg-gradient-to-t from-background from-70% to-transparent px-4 pb-4 pt-2 sm:px-6">
            <div className="mx-auto w-full max-w-3xl">{composer}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function AssistantMessage({
  message, toolPhase, onRetry,
}: {
  message: DisplayMessage;
  toolPhase: string | null;
  onRetry?: () => void;
}) {
  const { t } = useLocale();
  const [copied, setCopied] = useState(false);
  const waiting = message.pending && !message.content;

  return (
    <article className="group flex gap-3">
      <BrandMark className="mt-0.5 size-7 rounded-lg [&_svg]:size-4" />
      <div className="min-w-0 flex-1 space-y-3">
        {message.chatMode === "general" && (
          <Badge variant="secondary" className="gap-1"><Sparkles /> {t("generalBadge")}</Badge>
        )}

        {toolPhase ? (
          <div className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1.5 text-xs text-muted-foreground">
            <BookOpenText className="size-3.5 animate-pulse text-primary" />
            {toolPhase}
          </div>
        ) : waiting ? (
          <div className="flex items-center gap-1 py-2" aria-label="…">
            {[0, 150, 300].map((delay) => (
              <span key={delay} className="size-1.5 animate-bounce rounded-full bg-muted-foreground/60"
                    style={{ animationDelay: `${delay}ms` }} />
            ))}
          </div>
        ) : null}

        {message.content && (
          <div>
            <Markdown>{message.content}</Markdown>
            {message.pending && <span className="md-cursor" aria-hidden />}
          </div>
        )}

        {message.error && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{t("answerFailed")}</span>
            {onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}><RotateCw /> {t("tryAgain")}</Button>
            )}
          </div>
        )}

        {message.citations && message.citations.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              {t("sources")}{message.citationMode === "implicit" ? ` · ${t("consulted")}` : ""}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {message.citations.map((citation) => (
                <Tooltip key={citation.chunk_id}>
                  <TooltipTrigger asChild>
                    <a
                      href={`${API_BASE}/api/documents/${citation.document_id}/pages/${citation.page_start ?? 1}/image/`}
                      target="_blank"
                      rel="noreferrer"
                      className="flex max-w-64 items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs transition-colors hover:border-primary/40 hover:bg-accent"
                    >
                      <span className="tabular-nums font-semibold text-primary">{citation.n}</span>
                      <span className="truncate" dir="auto">{citation.title}</span>
                      {citation.page_start != null && (
                        <span className="tabular-nums shrink-0 text-muted-foreground">{t("pageShort")}{num(citation.page_start)}</span>
                      )}
                    </a>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-sm">
                    <p className="doc-text text-xs" dir="auto">{citation.quote}</p>
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          </div>
        )}

        {message.content && !message.pending && (
          <div className="flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
            <Hint label={copied ? t("copied") : t("copy")}>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t("copy")}
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(message.content);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  } catch {}
                }}
              >
                {copied ? <Check className="text-success" /> : <Copy />}
              </Button>
            </Hint>
          </div>
        )}
      </div>
    </article>
  );
}

