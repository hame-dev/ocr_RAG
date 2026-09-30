"use client";

import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, ArrowUpRight, BookOpenText, Check, Copy, FileText, Library, RotateCw, Sparkles, SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import {
  API_BASE, ChatAttachment, ChatMode, Citation, CodeRun, ConversationDetail, GeneratedFile, PhaseSummary, ResearchMode,
  SelectedDocument, ThinkingMode, api, createConversation, updateConversationScope,
} from "@/lib/api";
import { fmt, num, StringKey } from "@/lib/i18n";
import { PhaseEvent, streamChat } from "@/lib/sse";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger, Hint } from "@/components/ui/tooltip";
import { BrandMark } from "./app-shell/AppSidebar";
import { AttachmentList } from "./chat/AttachmentList";
import { CodeRunPanel } from "./chat/CodeRunPanel";
import { GeneratedFiles } from "./chat/GeneratedFiles";
import { ResearchProgress } from "./chat/ResearchProgress";
import { ThinkingPanel } from "./chat/ThinkingPanel";
import { ChatComposer, OutgoingTurn } from "./ChatComposer";
import { Markdown } from "./Markdown";
import { useLocale } from "./Providers";

interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  citationMode?: string;
  chatMode?: ChatMode;
  pending?: boolean;
  error?: string;
  /** Sent in this session (not loaded from history): plays the entrance. */
  fresh?: boolean;
  attachments?: ChatAttachment[];
  thinking?: ThinkingMode;
  reasoning?: string;
  thinkingMs?: number | null;
  /** When the first reasoning arrived (drives the live "Thinking… 12s" timer). */
  thinkingStartedAt?: number | null;
  /** When the answer started, i.e. thinking ended (until the server's exact figure arrives). */
  thinkingEndedAt?: number | null;
  /** Live deep think / deep research progress for the streaming message. */
  phases?: PhaseEvent[];
  phaseSummary?: PhaseSummary | null;
  followUps?: string[];
  /** General mode's code runs, and the charts / files they produced. */
  codeRuns?: CodeRun[];
  files?: GeneratedFile[];
}

interface Turn {
  content: string;
  researchMode: ResearchMode;
  chatMode: ChatMode;
  thinking: ThinkingMode;
  attachmentIds: string[];
}

const SUGGESTIONS: Record<ChatMode, StringKey[]> = {
  documents: ["suggestDoc1", "suggestDoc2", "suggestDoc3"],
  general: ["suggestGen1", "suggestGen2", "suggestGen3"],
};

// The chip shown while a tool runs, by the phase the server reports for it.
const TOOL_PHASES: Record<string, { label: StringKey; icon: LucideIcon }> = {
  searching: { label: "searching", icon: BookOpenText },
  comparing: { label: "comparingSources", icon: BookOpenText },
  verifying: { label: "verifyingEvidence", icon: BookOpenText },
  reviewing: { label: "reviewingSources", icon: BookOpenText },
  running_code: { label: "runningCode", icon: SquareTerminal },
};

// Strong ease-in-out for on-screen movement (the composer travelling from the
// centre of the empty state to the bottom dock).
const EASE_IN_OUT = "cubic-bezier(0.77, 0, 0.175, 1)";

export function ChatWorkspace({ conversationId }: { conversationId?: string }) {
  const { t } = useLocale();
  const pathname = usePathname();
  const queryClient = useQueryClient();

  // A chat started from /chat is created *in place*: the URL is swapped with
  // history.pushState instead of navigating, so nothing remounts, no route is
  // fetched, and the question appears the instant Enter is pressed.
  const [createdId, setCreatedId] = useState<string | null>(null);
  const activeId = conversationId ?? createdId ?? undefined;

  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [selectedSources, setSelectedSources] = useState<SelectedDocument[]>([]);
  const [streaming, setStreaming] = useState(false);
  /** The running tool's phase id (a TOOL_PHASES key), or null. */
  const [toolPhase, setToolPhase] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [composerMode, setComposerMode] = useState<ChatMode>("documents");
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null);
  const hydratedIdRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const dockTopRef = useRef<number | null>(null);
  const pinnedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const lastTurnRef = useRef<Turn | null>(null);

  const conversationQuery = useQuery({
    queryKey: ["conversation", activeId],
    queryFn: () => api<ConversationDetail>(`/api/conversations/${activeId}/`),
    enabled: Boolean(activeId),
  });

  function reset() {
    abortRef.current?.abort();
    // Detach the stream: its abort handler must not write into the cleared
    // transcript (it used to update a message that no longer existed).
    abortRef.current = null;
    hydratedIdRef.current = null;
    setMessages([]);
    setSelectedSources([]);
    setToolPhase(null);
    setSourceError(null);
  }

  // A different conversation route: start clean.
  useEffect(() => {
    reset();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // "New chat" (or Back) after an in-place creation lands on /chat again
  // without remounting this component, so clear it here. Only on a *change*
  // to /chat: Next applies the pushState URL asynchronously, so right after
  // creation there is a render where the path still reads /chat.
  const previousPathRef = useRef(pathname);
  useEffect(() => {
    const previous = previousPathRef.current;
    previousPathRef.current = pathname;
    if (!conversationId && previous !== pathname && pathname === "/chat") {
      reset();
      setCreatedId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, conversationId]);

  // Load history once per conversation. Never re-hydrate: a refetch (e.g. for
  // the title) must not overwrite a transcript that is streaming. Wait for an
  // in-flight refetch first: cached data can predate a turn that streamed
  // after the user navigated away, and hydrating from it would hide that turn.
  useEffect(() => {
    const conversation = conversationQuery.data;
    if (!activeId || !conversation || hydratedIdRef.current === activeId) return;
    if (conversationQuery.isFetching) return;
    hydratedIdRef.current = activeId;
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
          attachments: message.attachments,
          thinking: message.thinking,
          reasoning: message.reasoning,
          thinkingMs: message.thinking_ms,
          phaseSummary: message.phases,
          followUps: message.follow_ups,
          codeRuns: message.tool_calls,
          files: message.files,
        })),
    );
    setSelectedSources(conversation.selected_documents ?? []);
  }, [activeId, conversationQuery.data, conversationQuery.isFetching]);

  // Follow the stream. Instant and before paint: a smooth scroll restarted on
  // every token is what made streaming stutter.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && pinnedRef.current) element.scrollTop = element.scrollHeight;
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
    if (!activeId) return;
    try {
      const updated = await updateConversationScope(activeId, next.map((document) => document.id));
      setSelectedSources(updated.selected_documents ?? next);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversation", activeId] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    } catch (error) {
      setSelectedSources(previous);
      setSourceError(error instanceof Error ? error.message : t("scopeUpdateFailed"));
    }
  }

  /** Optimistically show the question and a pending answer. */
  function appendTurn(content: string, turn: Pick<Turn, "chatMode" | "thinking">, attachments: ChatAttachment[] = []) {
    // Remember where the composer is, so it can glide to the dock (FLIP).
    dockTopRef.current = dockRef.current?.getBoundingClientRect().top ?? null;
    pinnedRef.current = true;
    setMessages((current) => [
      ...current,
      { role: "user", content, chatMode: turn.chatMode, attachments, fresh: true },
      {
        role: "assistant", content: "", pending: true, chatMode: turn.chatMode,
        thinking: turn.thinking, reasoning: "", phases: [], fresh: true,
      },
    ]);
  }

  async function submit(content: string, outgoing: OutgoingTurn) {
    if (streaming) return;
    setSourceError(null);
    const turn: Turn = {
      content,
      researchMode: outgoing.researchMode,
      chatMode: outgoing.chatMode,
      thinking: outgoing.thinking,
      attachmentIds: outgoing.attachments.map((a) => a.id),
    };
    if (activeId) {
      appendTurn(content, turn, outgoing.attachments);
      await streamTurn(activeId, turn);
      return;
    }

    appendTurn(content, turn, outgoing.attachments);
    setStreaming(true);
    let created: ConversationDetail;
    try {
      created = await createConversation(
        selectedSources.length ? "selected" : "all",
        selectedSources.map((document) => document.id),
      );
    } catch (error) {
      // Roll back and hand the text back so nothing the user typed is lost.
      setMessages([]);
      setStreaming(false);
      setDraft({ text: content, nonce: Date.now() });
      setSourceError(error instanceof Error ? error.message : t("conversationCreateFailed"));
      return;
    }
    hydratedIdRef.current = created.id;
    queryClient.setQueryData(["conversation", created.id], created);
    setCreatedId(created.id);
    window.history.pushState(null, "", `/chat/${created.id}`);
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    await streamTurn(created.id, turn);
  }

  /** Follow-up chips: ask with the same settings as the turn that suggested them. */
  function askFollowUp(question: string) {
    const last = lastTurnRef.current;
    void submit(question, {
      researchMode: last?.researchMode ?? "deep",
      chatMode: last?.chatMode ?? "documents",
      thinking: last?.thinking ?? "instant",
      attachments: [],
    });
  }

  function updateLastMessage(update: (message: DisplayMessage) => DisplayMessage) {
    setMessages((current) => {
      if (!current.length) return current;
      const next = [...current];
      next[next.length - 1] = update(next[next.length - 1]);
      return next;
    });
  }

  async function streamTurn(id: string, turn: Turn) {
    // A retry must not re-send attachments: they are already in the thread.
    lastTurnRef.current = { ...turn, attachmentIds: [] };
    const controller = new AbortController();
    abortRef.current = controller;
    // Only the stream the view is attached to may write into the transcript;
    // reset() detaches it when the user starts a new chat mid-answer.
    const updateLast = (update: (message: DisplayMessage) => DisplayMessage) => {
      if (abortRef.current === controller) updateLastMessage(update);
    };
    let settled = false;
    setStreaming(true);
    setToolPhase(null);

    // Tokens (answer and reasoning) arrive ~30/s; render at most once per
    // frame instead of per token.
    let buffered = "";
    let bufferedThought = "";
    let frame = 0;
    const flush = () => {
      frame = 0;
      if (!buffered && !bufferedThought) return;
      const chunk = buffered;
      const thought = bufferedThought;
      buffered = "";
      bufferedThought = "";
      updateLast((last) => ({
        ...last,
        content: last.content + chunk,
        reasoning: (last.reasoning ?? "") + thought,
        thinkingStartedAt: last.thinkingStartedAt ?? (thought ? Date.now() : null),
        thinkingEndedAt: last.thinkingEndedAt ?? (chunk && last.thinkingStartedAt ? Date.now() : null),
      }));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(flush);
    };
    const dropBuffer = () => {
      buffered = "";
      bufferedThought = "";
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
    };

    const fail = (detail: string) => {
      settled = true;
      dropBuffer();
      setToolPhase(null);
      updateLast((last) => ({ ...last, pending: false, error: detail }));
    };

    try {
      await streamChat(id, turn.content, turn, {
        onToken: (token) => {
          buffered += token;
          schedule();
        },
        onThinking: (thought) => {
          bufferedThought += thought;
          schedule();
        },
        onPhase: (phase) => updateLast((last) => ({ ...last, phases: [...(last.phases ?? []), phase] })),
        // The streamed text was a tool-calling turn ("let me search…"): drop it.
        onReset: () => {
          dropBuffer();
          updateLast((last) => ({ ...last, content: "" }));
        },
        onToolStart: (_name, _args, phase) => setToolPhase(phase ?? "searching"),
        onToolEnd: () => setToolPhase(null),
        onCodeRun: ({ files, ...run }) => {
          setToolPhase(null);
          updateLast((last) => ({
            ...last,
            codeRuns: [...(last.codeRuns ?? []), run],
            files: [...(last.files ?? []), ...files],
          }));
        },
        onDone: (payload) => {
          settled = true;
          dropBuffer();
          setToolPhase(null);
          updateLast((last) => ({
            ...last,
            content: payload.content,
            citations: payload.citations,
            citationMode: payload.citation_mode,
            reasoning: payload.reasoning ?? last.reasoning,
            thinkingMs: payload.thinking_ms,
            phaseSummary: payload.phases,
            followUps: payload.follow_ups ?? [],
            codeRuns: payload.tool_calls ?? last.codeRuns,
            files: payload.files ?? last.files,
            pending: false,
          }));
        },
        onError: fail,
      }, controller.signal);
      // The connection closed without a done or error frame (proxy timeout,
      // worker killed): keep what arrived and say the answer is incomplete,
      // rather than leaving it pending forever.
      if (!settled) {
        flush();
        fail(t("streamFailed"));
      }
    } catch (error) {
      if (controller.signal.aborted) {
        flush();
        setToolPhase(null);
        updateLast((last) => ({ ...last, pending: false }));
      } else {
        fail(error instanceof Error ? error.message : t("streamFailed"));
      }
    } finally {
      dropBuffer();
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      // Picks up the title the first turn assigned (hydration is one-shot).
      if (controller.signal.aborted) {
        // The server kept going (it saves the partial reply); drop the cached
        // transcript so coming back to this chat loads the saved version.
        queryClient.removeQueries({ queryKey: ["conversation", id], type: "inactive" });
      }
      void queryClient.invalidateQueries({ queryKey: ["conversation", id] });
    }
  }

  function retryLast() {
    if (!activeId || streaming) return;
    // Prefer the exact settings of the turn that failed in this session; for a
    // failure loaded from history, fall back to the last question as asked.
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    const turn: Turn | undefined = lastTurnRef.current ?? (lastUser && {
      content: lastUser.content,
      researchMode: "balanced" as ResearchMode,
      chatMode: lastUser.chatMode ?? composerMode,
      thinking: "instant" as ThinkingMode,
      attachmentIds: [],
    });
    if (!turn) return;
    appendTurn(turn.content, turn);
    void streamTurn(activeId, turn);
  }

  const loading = Boolean(activeId) && conversationQuery.isPending;
  const empty = !loading && messages.length === 0 && !streaming;

  // FLIP: the composer is one element in both layouts. When the empty state
  // gives way to the transcript, play it from its old position to the dock
  // instead of letting it teleport.
  useLayoutEffect(() => {
    const dock = dockRef.current;
    const from = dockTopRef.current;
    dockTopRef.current = null;
    if (!dock || from == null) return;
    const delta = from - dock.getBoundingClientRect().top;
    if (Math.abs(delta) < 2 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    dock.animate(
      [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
      { duration: 280, easing: EASE_IN_OUT },
    );
  }, [empty]);

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
        <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-4 py-8">
          <Skeleton className="ms-auto h-10 w-2/3 rounded-2xl" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="ms-auto h-10 w-1/2 rounded-2xl" />
        </div>
      ) : (
        <>
          {/* Transcript, or (empty) the greeting sitting just above the composer. */}
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className={cn(
              "scrollbar-thin min-h-0 flex-1 overflow-y-auto",
              empty && "flex flex-col justify-end",
            )}
          >
            {empty ? (
              <div className="mx-auto w-full max-w-2xl px-4 pb-8 pt-10 text-center">
                <BrandMark className="mx-auto mb-5 size-11 rounded-xl" />
                <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t("chatWelcome")}</h1>
                <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{t("chatWelcomeHint")}</p>
              </div>
            ) : (
              <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8 sm:px-6">
                {messages.map((message, index) =>
                  message.role === "user" ? (
                    <UserMessage
                      key={index}
                      content={message.content}
                      attachments={message.attachments}
                      fresh={message.fresh}
                    />
                  ) : (
                    <AssistantMessage
                      key={index}
                      message={message}
                      toolPhase={index === messages.length - 1 ? toolPhase : null}
                      onRetry={index === messages.length - 1 && !streaming ? retryLast : undefined}
                      onFollowUp={index === messages.length - 1 && !streaming ? askFollowUp : undefined}
                    />
                  ),
                )}
              </div>
            )}
          </div>

          <div
            ref={dockRef}
            className={cn(
              "shrink-0 px-4 sm:px-6",
              empty ? "pb-4" : "bg-gradient-to-t from-background from-70% to-transparent pb-4 pt-2",
            )}
          >
            <div className="mx-auto w-full max-w-3xl">
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
            </div>
          </div>

          {empty && (
            <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-4">
              <div className="mx-auto flex max-w-2xl flex-wrap justify-center gap-2 pt-2">
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
          )}
        </>
      )}
    </div>
  );
}

const UserMessage = memo(function UserMessage({
  content, attachments, fresh,
}: {
  content: string;
  attachments?: ChatAttachment[];
  fresh?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-end gap-2", fresh && "animate-message-in")}>
      {attachments && attachments.length > 0 && <AttachmentList attachments={attachments} className="max-w-[85%]" />}
      <div className="max-w-[85%] rounded-2xl rounded-ee-md bg-muted px-4 py-2.5">
        <Markdown>{content}</Markdown>
      </div>
    </div>
  );
});

const AssistantMessage = memo(function AssistantMessage({
  message, toolPhase, onRetry, onFollowUp,
}: {
  message: DisplayMessage;
  toolPhase: string | null;
  onRetry?: () => void;
  onFollowUp?: (question: string) => void;
}) {
  const { t } = useLocale();
  const [copied, setCopied] = useState(false);
  const hasPhases = Boolean(message.phases?.length || message.phaseSummary);
  const thinkingLive = Boolean(message.pending && !message.content && message.reasoning);
  const waiting = message.pending && !message.content && !message.reasoning && !message.phases?.length;
  const toolChip = toolPhase ? TOOL_PHASES[toolPhase] ?? TOOL_PHASES.searching : null;

  return (
    <article className={cn("group flex gap-3", message.fresh && "animate-message-in")}>
      <BrandMark className="mt-0.5 size-7 rounded-lg p-0.5" />
      <div className="min-w-0 flex-1 space-y-3">
        {message.chatMode === "general" && (
          <Badge variant="secondary" className="gap-1"><Sparkles /> {t("generalBadge")}</Badge>
        )}

        {hasPhases && (
          <ResearchProgress
            phases={message.phases ?? []}
            summary={message.phaseSummary}
            live={Boolean(message.pending && !message.content)}
          />
        )}

        {message.reasoning ? (
          <ThinkingPanel
            reasoning={message.reasoning}
            live={thinkingLive}
            thinkingMs={
              message.thinkingMs
              ?? (message.thinkingStartedAt && message.thinkingEndedAt
                ? message.thinkingEndedAt - message.thinkingStartedAt
                : null)
            }
            startedAt={message.thinkingStartedAt}
          />
        ) : null}

        {message.codeRuns?.map((run, index) => <CodeRunPanel key={index} run={run} />)}

        {toolChip ? (
          <div className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1.5 text-xs text-muted-foreground">
            <toolChip.icon className="size-3.5 animate-pulse text-primary" />
            {t(toolChip.label)}
          </div>
        ) : waiting ? (
          <div className="flex items-center gap-1 py-2" role="status" aria-label={t("assistantAnswer")}>
            {[0, 150, 300].map((delay) => (
              <span key={delay} className="size-1.5 animate-typing-dot rounded-full bg-muted-foreground"
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

        {message.files && message.files.length > 0 && <GeneratedFiles files={message.files} />}

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

        {onFollowUp && message.followUps && message.followUps.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">{t("followUps")}</p>
            <div className="flex flex-col items-start gap-1.5">
              {message.followUps.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => onFollowUp(question)}
                  className="group/fu flex max-w-full items-center gap-2 rounded-lg border bg-card px-3 py-1.5 text-start text-sm transition-colors hover:border-primary/40 hover:bg-accent"
                  dir="auto"
                >
                  <span className="min-w-0">{question}</span>
                  <ArrowUpRight className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover/fu:text-primary rtl:-scale-x-100" />
                </button>
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
});
