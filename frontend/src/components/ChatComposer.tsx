"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, BookOpenText, Sparkles, Square, X } from "lucide-react";
import { ChatMode, ResearchMode, SelectedDocument } from "@/lib/api";
import { StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Hint } from "@/components/ui/tooltip";
import { useLocale } from "./Providers";
import { SourcePicker } from "./SourcePicker";

const RESEARCH_MODE_KEY = "ocr-rag-chat-research-mode";
const CHAT_MODE_KEY = "ocr-rag-chat-mode";

const DEPTHS: { value: ResearchMode; label: StringKey; hint: StringKey }[] = [
  { value: "fast", label: "fast", hint: "fastHint" },
  { value: "balanced", label: "balanced", hint: "balancedHint" },
  { value: "deep", label: "deep", hint: "deepHint" },
];

interface ChatComposerProps {
  selectedSources: SelectedDocument[];
  streaming: boolean;
  sourceError?: string | null;
  onSourcesChange: (documents: SelectedDocument[]) => Promise<void> | void;
  onSend: (content: string, mode: ResearchMode, chatMode: ChatMode) => Promise<void> | void;
  onStop?: () => void;
  /** Lets a parent (e.g. suggestion chips) prefill the input. */
  draft?: { text: string; nonce: number } | null;
  onModeChange?: (mode: ChatMode) => void;
}

export function ChatComposer({
  selectedSources, streaming, sourceError, onSourcesChange, onSend, onStop, draft, onModeChange,
}: ChatComposerProps) {
  const { t } = useLocale();
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ResearchMode>("balanced");
  const [chatMode, setChatMode] = useState<ChatMode>("documents");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const general = chatMode === "general";

  useEffect(() => {
    try {
      const stored = localStorage.getItem(RESEARCH_MODE_KEY);
      if (stored === "fast" || stored === "balanced" || stored === "deep") setMode(stored);
      const storedChat = localStorage.getItem(CHAT_MODE_KEY);
      if (storedChat === "documents" || storedChat === "general") {
        setChatMode(storedChat);
        onModeChange?.(storedChat);
      }
    } catch {
      /* storage unavailable: keep the defaults */
    }
    // One-time restore on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!draft) return;
    setInput(draft.text);
    textareaRef.current?.focus();
  }, [draft]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, [input]);

  function changeMode(next: ResearchMode) {
    setMode(next);
    try { localStorage.setItem(RESEARCH_MODE_KEY, next); } catch {}
  }

  function changeChatMode(next: ChatMode) {
    setChatMode(next);
    onModeChange?.(next);
    try { localStorage.setItem(CHAT_MODE_KEY, next); } catch {}
    textareaRef.current?.focus();
  }

  async function submit() {
    const content = input.trim();
    if (!content || streaming) return;
    setInput("");
    await onSend(content, mode, chatMode);
  }

  return (
    <div className="w-full">
      <div className="rounded-2xl border bg-card shadow-sm transition-shadow focus-within:border-ring/50 focus-within:shadow-md">
        {!general && selectedSources.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-3">
            {selectedSources.map((document) => (
              <span
                key={document.id}
                className="flex max-w-full items-center gap-1 rounded-md border bg-muted/50 py-0.5 pe-1 ps-2 text-xs"
              >
                <span className="max-w-48 truncate" dir="auto">{document.display_title}</span>
                <button
                  type="button"
                  onClick={() => void onSourcesChange(selectedSources.filter((item) => item.id !== document.id))}
                  disabled={streaming}
                  className="grid size-4 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label={`${t("removeSource")}: ${document.display_title}`}
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          dir="auto"
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={general ? t("askGeneral") : t("askAnything")}
          className="block min-h-[3.25rem] w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] leading-relaxed placeholder:text-muted-foreground focus:outline-none"
        />

        <div className="flex flex-wrap items-center gap-1.5 px-2 pb-2">
          <Segmented<ChatMode>
            size="sm"
            value={chatMode}
            onChange={changeChatMode}
            disabled={streaming}
            aria-label={t("chatMode")}
            options={[
              { value: "documents", label: t("modeDocuments"), icon: <BookOpenText />, title: t("modeDocumentsHint") },
              { value: "general", label: t("modeGeneral"), icon: <Sparkles />, title: t("modeGeneralHint") },
            ]}
          />

          {/* Sources and research depth only mean something when documents are searched. */}
          {!general && (
            <>
              <SourcePicker selected={selectedSources} disabled={streaming} onChange={onSourcesChange} />
              <Select value={mode} onValueChange={(value) => changeMode(value as ResearchMode)} disabled={streaming}>
                <SelectTrigger
                  className="h-8 w-auto gap-1.5 border-0 bg-transparent px-2.5 text-xs text-muted-foreground shadow-none hover:bg-accent hover:text-foreground"
                  aria-label={t("researchMode")}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent side="top" align="start">
                  {DEPTHS.map((depth) => (
                    <SelectItem key={depth.value} value={depth.value} description={t(depth.hint)}>
                      {t(depth.label)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          )}

          {streaming && onStop ? (
            <Hint label={t("stop")}>
              <Button size="icon-sm" variant="secondary" onClick={onStop} className="ms-auto rounded-full" aria-label={t("stop")}>
                <Square className="size-3 fill-current" />
              </Button>
            </Hint>
          ) : (
            <Button
              size="icon-sm"
              onClick={() => void submit()}
              disabled={streaming || !input.trim()}
              className={cn("ms-auto rounded-full")}
              aria-label={t("send")}
            >
              <ArrowUp />
            </Button>
          )}
        </div>
      </div>

      {sourceError && <p className="mt-2 px-2 text-xs text-destructive" role="alert">{sourceError}</p>}
      <p className="mt-2 text-center text-[11px] text-muted-foreground">
        {general ? t("generalDisclaimer") : t("researchDisclaimer")}
      </p>
    </div>
  );
}
