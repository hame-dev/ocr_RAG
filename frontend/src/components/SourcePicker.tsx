"use client";

import { useDeferredValue, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, DocumentSummary, SelectedDocument } from "@/lib/api";
import { useLocale } from "./Providers";

interface SourcePickerProps {
  selected: SelectedDocument[];
  disabled?: boolean;
  onChange: (documents: SelectedDocument[]) => Promise<void> | void;
}

export function SourcePicker({ selected, disabled, onChange }: SourcePickerProps) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim());
  const rootRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["chat-source-documents", deferredQuery],
    queryFn: () =>
      api<{ results: DocumentSummary[] }>(
        `/api/documents/?status=ready&limit=50${
          deferredQuery ? `&q=${encodeURIComponent(deferredQuery)}` : ""
        }`,
      ),
    enabled: open,
  });

  useEffect(() => {
    if (!open) return;
    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const selectedIds = new Set(selected.map((document) => document.id));
  const documents = data?.results ?? [];

  async function toggle(document: DocumentSummary) {
    const compact: SelectedDocument = {
      id: document.id,
      display_title: document.display_title,
      original_filename: document.original_filename,
    };
    const next = selectedIds.has(document.id)
      ? selected.filter((item) => item.id !== document.id)
      : [...selected, compact];
    await onChange(next);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="composer-control"
      >
        <span aria-hidden>＋</span>
        <span>
          {selected.length
            ? t("selectedSourceCount").replace("{count}", String(selected.length))
            : t("sources")}
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label={t("selectSources")}
          className="absolute bottom-full start-0 z-30 mb-2 flex max-h-[26rem] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border shadow-2xl"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}
        >
          <div className="border-b p-3" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center gap-2">
              <div>
                <p className="text-sm font-semibold">{t("selectSources")}</p>
                <p className="text-xs" style={{ color: "var(--muted)" }}>
                  {t("sourcePickerHint")}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="ms-auto grid h-8 w-8 place-items-center rounded-lg hover:bg-[var(--hover)]"
                aria-label={t("close")}
              >
                ×
              </button>
            </div>
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("searchSources")}
              className="mt-3 w-full rounded-xl border px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[var(--accent)]/30"
              style={{ background: "var(--bg)", borderColor: "var(--border)", color: "var(--fg)" }}
            />
          </div>

          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => void onChange([])}
              className="mx-3 mt-3 rounded-xl border px-3 py-2 text-start text-sm hover:bg-[var(--hover)]"
              style={{ borderColor: "var(--border)" }}
            >
              <span className="font-medium">{t("wholeLibrary")}</span>
              <span className="ms-2 text-xs" style={{ color: "var(--muted)" }}>
                {t("clearSourceFilter")}
              </span>
            </button>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {isLoading ? (
              <p className="py-6 text-center text-sm" style={{ color: "var(--muted)" }}>
                {t("loading")}
              </p>
            ) : documents.length === 0 ? (
              <p className="py-6 text-center text-sm" style={{ color: "var(--muted)" }}>
                {t("noMatchingSources")}
              </p>
            ) : (
              <ul className="space-y-1">
                {documents.map((document) => {
                  const checked = selectedIds.has(document.id);
                  return (
                    <li key={document.id}>
                      <label className="flex cursor-pointer items-start gap-3 rounded-xl px-2.5 py-2 hover:bg-[var(--hover)]">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => void toggle(document)}
                          className="mt-1 h-4 w-4 accent-[var(--accent)]"
                        />
                        <span className="min-w-0">
                          <span className="doc-text block truncate text-sm font-medium" dir="auto">
                            {document.display_title}
                          </span>
                          {document.display_title !== document.original_filename && (
                            <span className="doc-text block truncate text-xs" dir="auto"
                                  style={{ color: "var(--muted)" }}>
                              {document.original_filename}
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
