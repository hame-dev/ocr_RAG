"use client";

import { useState } from "react";
import { num } from "@/lib/i18n";
import { useLocale } from "./Providers";

interface Column {
  run_id: string;
  engine: string;
  text: string;
  confidence: number | null;
  gibberish_score: number | null;
  char_count: number;
  duration_ms: number | null;
  warnings: string[];
}

interface Page {
  page_number: number;
  columns: Column[];
  consensus: { locked_spans: number[][]; agreement: number };
}

/**
 * Side-by-side engine comparison.
 *
 * Columns fill in as each engine finishes rather than all at the end — that is
 * what makes the partial-results architecture visible to the user.
 *
 * Spans where every engine agreed are tinted green: it lets the eye skip
 * straight to the parts that actually differ, which is the whole point of
 * running several engines.
 */
export function OcrComparisonGrid({
  comparison,
  pendingEngines,
  onSelect,
}: {
  comparison: { pages: Page[]; ranking: string[]; baseline_run_id: string | null } | null;
  pendingEngines: string[];
  onSelect: (runId: string) => void;
}) {
  const { t } = useLocale();
  const [pageIndex, setPageIndex] = useState(0);

  const pages = comparison?.pages ?? [];
  const page = pages[pageIndex];
  const columnCount = (page?.columns.length ?? 0) + pendingEngines.length;

  if (!page && !pendingEngines.length) return null;

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="font-medium">{t("comparison")}</h2>
        {page && (
          <span className="numeric text-sm" style={{ color: "var(--muted)" }}>
            {t("page")} {num(page.page_number)} {t("of")} {num(pages.length)}
          </span>
        )}
        {page && page.consensus.agreement > 0 && (
          <span className="numeric text-xs" style={{ color: "var(--muted)" }}>
            · {num(page.consensus.agreement * 100)}% agreement
          </span>
        )}
        {pages.length > 1 && (
          <div className="ms-auto flex gap-1">
            <button
              className="rounded border px-2 py-1 text-sm disabled:opacity-40"
              style={{ borderColor: "var(--border)" }}
              disabled={pageIndex === 0}
              onClick={() => setPageIndex((i) => i - 1)}
            >
              <span className="flip-rtl inline-block">←</span>
            </button>
            <button
              className="rounded border px-2 py-1 text-sm disabled:opacity-40"
              style={{ borderColor: "var(--border)" }}
              disabled={pageIndex >= pages.length - 1}
              onClick={() => setPageIndex((i) => i + 1)}
            >
              <span className="flip-rtl inline-block">→</span>
            </button>
          </div>
        )}
      </div>

      {/* Wide content scrolls inside its own container; the page body never
          scrolls horizontally. */}
      <div className="overflow-x-auto">
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${Math.max(columnCount, 1)}, minmax(280px, 1fr))` }}
        >
          {page?.columns.map((col) => (
            <ColumnCard
              key={col.run_id}
              col={col}
              locked={page.consensus.locked_spans}
              onSelect={() => onSelect(col.run_id)}
            />
          ))}

          {/* Skeletons appear immediately for engines still running, so the
              grid shows what is coming rather than growing unpredictably. */}
          {pendingEngines.map((engine) => (
            <div
              key={engine}
              className="animate-pulse rounded-lg border p-3"
              style={{ borderColor: "var(--border)" }}
            >
              <div className="mb-2 text-sm font-medium opacity-60">{engine}</div>
              <div className="space-y-2">
                {[...Array(6)].map((_, i) => (
                  <div
                    key={i}
                    className="h-3 rounded"
                    style={{ background: "var(--border)", width: `${70 + (i % 3) * 10}%` }}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ColumnCard({
  col,
  locked,
  onSelect,
}: {
  col: Column;
  locked: number[][];
  onSelect: () => void;
}) {
  const { t } = useLocale();
  const suspect = col.warnings?.includes("suspected_summarization");

  return (
    <div className="flex flex-col rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{col.engine}</span>
        <span className="ms-auto numeric text-xs" style={{ color: "var(--muted)" }}>
          {col.duration_ms != null ? `${num(col.duration_ms / 1000, 1)}s` : ""}
        </span>
      </div>

      <div className="mb-2 flex items-center gap-3 text-xs" style={{ color: "var(--muted)" }}>
        {col.confidence != null && (
          <span className="numeric">
            {t("confidence")} {num(col.confidence * 100)}%
          </span>
        )}
        <span className="numeric">
          {num(col.char_count)} {t("characters")}
        </span>
      </div>

      {suspect && (
        <p className="mb-2 rounded px-2 py-1 text-xs" style={{ background: "#fef3c7", color: "#92400e" }}>
          This result is much shorter than the others — the model may have
          summarized instead of transcribing.
        </p>
      )}

      <div
        className="doc-text mb-3 max-h-96 flex-1 overflow-y-auto rounded p-2 text-sm"
        dir="auto"
        style={{ background: "var(--bg)" }}
      >
        <ConsensusText text={col.text} locked={locked} />
      </div>

      <button
        onClick={onSelect}
        className="rounded-md px-3 py-1.5 text-sm text-white"
        style={{ background: "var(--accent)" }}
      >
        {t("selectThis")}
      </button>
    </div>
  );
}

/** Tint spans where every engine agreed, so the eye goes to the differences. */
function ConsensusText({ text, locked }: { text: string; locked: number[][] }) {
  if (!locked?.length) return <>{text}</>;

  const parts: React.ReactNode[] = [];
  let cursor = 0;

  for (const [start, end] of locked) {
    if (start > cursor) parts.push(<span key={`d${cursor}`}>{text.slice(cursor, start)}</span>);
    parts.push(
      <span key={`a${start}`} style={{ background: "rgba(34,197,94,0.16)" }}>
        {text.slice(start, end)}
      </span>,
    );
    cursor = end;
  }
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}
