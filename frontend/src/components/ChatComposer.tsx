"use client";

import { useEffect, useRef, useState } from "react";
import { ResearchMode, SelectedDocument } from "@/lib/api";
import { useLocale } from "./Providers";
import { SourcePicker } from "./SourcePicker";

const RESEARCH_MODE_KEY = "ocr-rag-chat-research-mode";

interface ChatComposerProps {
  selectedSources: SelectedDocument[];
  streaming: boolean;
  sourceError?: string | null;
  onSourcesChange: (documents: SelectedDocument[]) => Promise<void> | void;
  onSend: (content: string, mode: ResearchMode) => Promise<void> | void;
}

export function ChatComposer({
  selectedSources,
  streaming,
  sourceError,
  onSourcesChange,
  onSend,
}: ChatComposerProps) {
  const { t } = useLocale();
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ResearchMode>("balanced");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const stored = localStorage.getItem(RESEARCH_MODE_KEY);
    if (stored === "fast" || stored === "balanced" || stored === "deep") setMode(stored);
  }, []);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [input]);

  function changeMode(next: ResearchMode) {
    setMode(next);
    localStorage.setItem(RESEARCH_MODE_KEY, next);
  }

  async function submit() {
    const content = input.trim();
    if (!content || streaming) return;
    setInput("");
    await onSend(content, mode);
  }

  return (
    <div className="w-full">
      {selectedSources.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5 px-1">
          {selectedSources.map((document) => (
            <span
              key={document.id}
              className="flex max-w-full items-center gap-1 rounded-full border px-2.5 py-1 text-xs"
              style={{ background: "var(--card)", borderColor: "var(--border)" }}
            >
              <span className="doc-text max-w-48 truncate" dir="auto">
                {document.display_title}
              </span>
              <button
                type="button"
                onClick={() => void onSourcesChange(
                  selectedSources.filter((item) => item.id !== document.id),
                )}
                disabled={streaming}
                className="grid h-4 w-4 place-items-center rounded-full hover:bg-[var(--hover)]"
                aria-label={`${t("removeSource")}: ${document.display_title}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="composer-shell">
        <textarea
          ref={textareaRef}
          dir="auto"
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={t("askAnything")}
          disabled={streaming}
          className="doc-text min-h-12 w-full resize-none bg-transparent px-4 pt-3 text-[15px] outline-none placeholder:font-sans"
          style={{ color: "var(--fg)" }}
        />

        <div className="flex items-center gap-2 px-2 pb-2">
          <SourcePicker
            selected={selectedSources}
            disabled={streaming}
            onChange={onSourcesChange}
          />

          <label className="relative">
            <span className="sr-only">{t("researchMode")}</span>
            <select
              value={mode}
              onChange={(event) => changeMode(event.target.value as ResearchMode)}
              disabled={streaming}
              className="composer-control appearance-none pe-7"
              title={t(`${mode}Hint` as "fastHint" | "balancedHint" | "deepHint")}
            >
              <option value="fast">{t("fast")}</option>
              <option value="balanced">{t("balanced")}</option>
              <option value="deep">{t("deep")}</option>
            </select>
            <span className="pointer-events-none absolute end-2 top-1/2 -translate-y-1/2 text-[10px]"
                  style={{ color: "var(--muted)" }} aria-hidden>
              ▾
            </span>
          </label>

          <button
            type="button"
            onClick={() => void submit()}
            disabled={streaming || !input.trim()}
            className="ms-auto grid h-9 w-9 place-items-center rounded-full text-lg text-white transition disabled:opacity-35"
            style={{ background: "var(--accent)" }}
            aria-label={t("send")}
          >
            ↑
          </button>
        </div>
      </div>

      {sourceError && (
        <p className="mt-2 px-2 text-xs text-red-600" role="alert">
          {sourceError}
        </p>
      )}
      <p className="mt-2 text-center text-[11px]" style={{ color: "var(--muted)" }}>
        {t("researchDisclaimer")}
      </p>
    </div>
  );
}
