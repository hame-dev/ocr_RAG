"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertCircle, ArrowUp, BookOpenText, Brain, FileText, Loader2, Paperclip, Sparkles, Square, X, Zap,
} from "lucide-react";
import {
  ApiError, ChatAttachment, ChatMode, ResearchMode, SelectedDocument, ThinkingMode,
  deleteAttachment, uploadAttachment,
} from "@/lib/api";
import { bytes } from "@/lib/format";
import { fmt, StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Hint } from "@/components/ui/tooltip";
import { useLocale } from "./Providers";
import { SourcePicker } from "./SourcePicker";

const RESEARCH_MODE_KEY = "ocr-rag-chat-research-mode";
const CHAT_MODE_KEY = "ocr-rag-chat-mode";
const THINKING_KEY = "ocr-rag-chat-thinking";
const MAX_FILES = 5;
const ACCEPT = "image/*,.pdf,.txt,.md,.csv,.tsv,.json,.docx";

const DEPTHS: { value: ResearchMode; label: StringKey; hint: StringKey }[] = [
  { value: "fast", label: "fast", hint: "fastHint" },
  { value: "balanced", label: "balanced", hint: "balancedHint" },
  { value: "deep", label: "deep", hint: "deepHint" },
];

const THINKING: { value: ThinkingMode; label: StringKey; hint: StringKey; icon: typeof Zap }[] = [
  { value: "instant", label: "thinkInstant", hint: "thinkInstantHint", icon: Zap },
  { value: "think", label: "thinkThink", hint: "thinkThinkHint", icon: Brain },
  { value: "deep", label: "thinkDeep", hint: "thinkDeepHint", icon: Sparkles },
];

export interface OutgoingTurn {
  researchMode: ResearchMode;
  chatMode: ChatMode;
  thinking: ThinkingMode;
  attachments: ChatAttachment[];
}

interface PendingFile {
  key: string;
  file: File;
  localPreview: string | null;
  progress: number;
  status: "uploading" | "ready" | "error";
  attachment?: ChatAttachment;
  error?: string;
  abort: AbortController;
}

interface ChatComposerProps {
  selectedSources: SelectedDocument[];
  streaming: boolean;
  sourceError?: string | null;
  onSourcesChange: (documents: SelectedDocument[]) => Promise<void> | void;
  onSend: (content: string, turn: OutgoingTurn) => Promise<void> | void;
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
  const [thinking, setThinking] = useState<ThinkingMode>("instant");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const general = chatMode === "general";
  const uploading = files.some((f) => f.status === "uploading");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(RESEARCH_MODE_KEY);
      if (stored === "fast" || stored === "balanced" || stored === "deep") setMode(stored);
      const storedChat = localStorage.getItem(CHAT_MODE_KEY);
      if (storedChat === "documents" || storedChat === "general") {
        setChatMode(storedChat);
        onModeChange?.(storedChat);
      }
      const storedThinking = localStorage.getItem(THINKING_KEY);
      if (storedThinking === "instant" || storedThinking === "think" || storedThinking === "deep") {
        setThinking(storedThinking);
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

  // Release object URLs for thumbnails when the composer goes away.
  const filesRef = useRef(files);
  filesRef.current = files;
  useEffect(() => () => filesRef.current.forEach((f) => f.localPreview && URL.revokeObjectURL(f.localPreview)), []);

  function persist(key: string, value: string) {
    try { localStorage.setItem(key, value); } catch {}
  }

  function changeChatMode(next: ChatMode) {
    setChatMode(next);
    onModeChange?.(next);
    persist(CHAT_MODE_KEY, next);
    textareaRef.current?.focus();
  }

  function patch(key: string, update: Partial<PendingFile>) {
    setFiles((current) => current.map((f) => (f.key === key ? { ...f, ...update } : f)));
  }

  function addFiles(list: FileList | File[]) {
    setFileError(null);
    const incoming = Array.from(list);
    const room = MAX_FILES - files.length;
    if (incoming.length > room) setFileError(fmt(t("tooManyAttachments"), { n: MAX_FILES }));
    for (const file of incoming.slice(0, Math.max(room, 0))) {
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`;
      const abort = new AbortController();
      const localPreview = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      setFiles((current) => [...current, { key, file, localPreview, progress: 0, status: "uploading", abort }]);
      uploadAttachment(file, (progress) => patch(key, { progress }), abort.signal)
        .then((attachment) => patch(key, { status: "ready", attachment, progress: 1 }))
        .catch((error) => {
          if (abort.signal.aborted) return;
          patch(key, {
            status: "error",
            error: error instanceof ApiError && error.detail ? error.detail : t("attachmentFailed"),
          });
        });
    }
  }

  function removeFile(item: PendingFile) {
    item.abort.abort();
    if (item.localPreview) URL.revokeObjectURL(item.localPreview);
    if (item.attachment) void deleteAttachment(item.attachment.id).catch(() => undefined);
    setFiles((current) => current.filter((f) => f.key !== item.key));
  }

  async function submit() {
    const content = input.trim();
    if (!content || streaming || uploading) return;
    const ready = files.filter((f) => f.status === "ready" && f.attachment);
    setInput("");
    setFiles([]);
    setFileError(null);
    // Thumbnails now come from the server; the local copies can go.
    ready.forEach((f) => f.localPreview && URL.revokeObjectURL(f.localPreview));
    await onSend(content, {
      researchMode: mode,
      chatMode,
      thinking: general ? thinking : "instant",
      attachments: ready.map((f) => f.attachment!),
    });
  }

  return (
    <div className="w-full">
      <div
        onDragOver={(event) => {
          if (!general || !event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);
        }}
        onDrop={(event) => {
          if (!general) return;
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
        }}
        className={cn(
          "relative rounded-2xl border bg-card shadow-sm transition-[box-shadow,border-color] focus-within:border-ring/50 focus-within:shadow-md",
          dragging && "border-primary ring-2 ring-primary/20",
        )}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-2xl bg-primary/5 text-sm font-medium text-primary">
            {t("dropFilesHere")}
          </div>
        )}

        {!general && selectedSources.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-3">
            {selectedSources.map((document) => (
              <span key={document.id} className="flex max-w-full items-center gap-1 rounded-md border bg-muted/50 py-0.5 pe-1 ps-2 text-xs">
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

        {files.length > 0 && (
          <ul className="flex flex-wrap gap-2 px-3 pt-3">
            {files.map((item) => (
              <li key={item.key}>
                <AttachmentChip item={item} onRemove={() => removeFile(item)} removeLabel={t("removeAttachment")} />
              </li>
            ))}
          </ul>
        )}

        <textarea
          ref={textareaRef}
          dir="auto"
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onPaste={(event) => {
            if (!general) return;
            const pasted = Array.from(event.clipboardData.files).filter((f) => f.type.startsWith("image/"));
            if (pasted.length) {
              event.preventDefault();
              addFiles(pasted);
            }
          }}
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

          {general ? (
            <>
              <Hint label={t("attachFiles")}>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-muted-foreground"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={streaming || files.length >= MAX_FILES}
                  aria-label={t("attachFiles")}
                >
                  <Paperclip />
                </Button>
              </Hint>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPT}
                hidden
                onChange={(event) => {
                  if (event.target.files?.length) addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <ModeSelect
                value={thinking}
                onChange={(value) => { setThinking(value); persist(THINKING_KEY, value); }}
                disabled={streaming}
                label={t("thinkingLevel")}
                options={THINKING.map((o) => ({ ...o, label: t(o.label), hint: t(o.hint) }))}
              />
            </>
          ) : (
            <>
              <SourcePicker selected={selectedSources} disabled={streaming} onChange={onSourcesChange} />
              <ModeSelect
                value={mode}
                onChange={(value) => { setMode(value); persist(RESEARCH_MODE_KEY, value); }}
                disabled={streaming}
                label={t("researchMode")}
                options={DEPTHS.map((o) => ({ ...o, label: t(o.label), hint: t(o.hint) }))}
              />
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
              disabled={streaming || uploading || !input.trim()}
              className="ms-auto rounded-full"
              aria-label={t("send")}
            >
              {uploading ? <Loader2 className="animate-spin" /> : <ArrowUp />}
            </Button>
          )}
        </div>
      </div>

      {(sourceError || fileError) && (
        <p className="mt-2 px-2 text-xs text-destructive" role="alert">{sourceError || fileError}</p>
      )}
      <p className="mt-2 text-center text-[11px] text-muted-foreground">
        {general ? t("generalDisclaimer") : t("researchDisclaimer")}
      </p>
    </div>
  );
}

function ModeSelect<T extends string>({
  value, onChange, disabled, label, options,
}: {
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
  label: string;
  options: { value: T; label: string; hint: string; icon?: typeof Zap }[];
}) {
  const current = options.find((o) => o.value === value);
  const Icon = current?.icon;
  return (
    <Select value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <SelectTrigger
        className="h-8 w-auto gap-1.5 border-0 bg-transparent px-2.5 text-xs text-muted-foreground shadow-none hover:bg-accent hover:text-foreground"
        aria-label={label}
      >
        {Icon && <Icon className="size-3.5" />}
        <SelectValue />
      </SelectTrigger>
      <SelectContent side="top" align="start" className="w-72">
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} description={option.hint}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function AttachmentChip({ item, onRemove, removeLabel }: { item: PendingFile; onRemove: () => void; removeLabel: string }) {
  const failed = item.status === "error";
  return (
    <div
      className={cn(
        "group relative flex h-14 max-w-56 items-center gap-2.5 rounded-xl border bg-muted/40 p-1.5 pe-3",
        failed && "border-destructive/40 bg-destructive/5",
      )}
      title={failed ? item.error : item.file.name}
    >
      <span className="relative grid size-11 shrink-0 place-items-center overflow-hidden rounded-lg bg-background text-muted-foreground">
        {item.localPreview ? (
          // eslint-disable-next-line @next/next/no-img-element -- local object URL
          <img src={item.localPreview} alt="" className="size-full object-cover" />
        ) : failed ? (
          <AlertCircle className="size-5 text-destructive" />
        ) : (
          <FileText className="size-5" />
        )}
        {item.status === "uploading" && <ProgressRing value={item.progress} />}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-xs font-medium" dir="auto">{item.file.name}</span>
        <span className={cn("block truncate text-[11px]", failed ? "text-destructive" : "text-muted-foreground")}>
          {failed ? item.error : bytes(item.file.size)}
        </span>
      </span>
      <button
        type="button"
        onClick={onRemove}
        className="absolute -end-1.5 -top-1.5 grid size-5 place-items-center rounded-full border bg-background text-muted-foreground shadow-sm transition-colors hover:text-foreground"
        aria-label={`${removeLabel}: ${item.file.name}`}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

/** Upload progress over the thumbnail. */
function ProgressRing({ value }: { value: number }) {
  const radius = 14;
  const circumference = 2 * Math.PI * radius;
  return (
    <span className="absolute inset-0 grid place-items-center bg-background/70">
      <svg viewBox="0 0 36 36" className="size-8 -rotate-90" aria-hidden>
        <circle cx="18" cy="18" r={radius} fill="none" stroke="currentColor" strokeWidth="3" className="text-border" />
        <circle
          cx="18" cy="18" r={radius} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"
          className="text-primary transition-[stroke-dashoffset] duration-200"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - Math.max(0.05, value))}
        />
      </svg>
    </span>
  );
}
