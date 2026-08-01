"use client";

import { Engine, OcrRun } from "@/lib/api";
import { num } from "@/lib/i18n";
import { useLocale } from "./Providers";

export interface EngineProgress {
  engine: string;
  status: "queued" | "running" | "succeeded" | "failed" | "timeout" | "skipped" | "cancelled";
  page: number;
  of: number;
  durationMs?: number | null;
  charCount?: number;
  error?: string;
}

/**
 * The live "system is processing your document" indicator.
 *
 * Each engine gets its own card and its own page-level bar, fed by the
 * `ocr_page_done` SSE events. Without per-page progress a slow VLM leaves the
 * user staring at a spinner for minutes with no idea whether anything is
 * happening.
 */
export function OcrProgressPanel({
  progress,
  engines,
  startedAt,
}: {
  progress: Record<string, EngineProgress>;
  engines: Engine[];
  startedAt: number | null;
}) {
  const { t, locale } = useLocale();
  const entries = Object.values(progress);
  if (!entries.length) return null;

  const active = entries.filter((e) => e.status === "queued" || e.status === "running");
  const elapsed = startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-3">
        {active.length > 0 && (
          <span
            className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
            style={{ color: "var(--accent)" }}
            aria-hidden
          />
        )}
        <h2 className="font-medium">
          {active.length > 0 ? t("processing") : t("comparison")}
        </h2>
        {startedAt && (
          <span className="ms-auto numeric text-sm" style={{ color: "var(--muted)" }}>
            {num(elapsed)}s
          </span>
        )}
      </div>

      <ul className="grid gap-3 sm:grid-cols-2">
        {entries.map((p) => {
          const meta = engines.find((e) => e.name === p.engine);
          const label =
            locale === "ar"
              ? meta?.display_name_ar ?? p.engine
              : meta?.display_name_en ?? p.engine;

          const pct = p.of > 0 ? Math.round((p.page / p.of) * 100) : 0;
          // ETA from the engine's own measured speed, so a slow VLM says so
          // up front rather than looking hung.
          const eta =
            meta && p.of > 0 && p.status === "running"
              ? Math.max(0, Math.round((p.of - p.page) * meta.est_seconds_per_page))
              : null;

          return (
            <li
              key={p.engine}
              className="rounded-lg border p-3"
              style={{ borderColor: "var(--border)" }}
            >
              <div className="flex items-center gap-2">
                <StatusDot status={p.status} />
                <span className="truncate text-sm font-medium">{label}</span>
                <span className="ms-auto numeric text-xs" style={{ color: "var(--muted)" }}>
                  {p.status === "succeeded" && p.durationMs != null
                    ? `${num(p.durationMs / 1000, 1)}s`
                    : p.of > 0
                      ? `${num(p.page)}/${num(p.of)}`
                      : ""}
                </span>
              </div>

              <div
                className="mt-2 h-1.5 w-full overflow-hidden rounded-full"
                style={{ background: "var(--border)" }}
              >
                <div
                  className="h-full transition-all duration-300"
                  style={{
                    width: `${p.status === "succeeded" ? 100 : pct}%`,
                    background:
                      p.status === "failed" || p.status === "timeout"
                        ? "#ef4444"
                        : "var(--accent)",
                  }}
                />
              </div>

              <p className="mt-1.5 text-xs" style={{ color: "var(--muted)" }}>
                {p.status === "failed" || p.status === "timeout"
                  ? p.error ?? p.status
                  : p.status === "succeeded"
                    ? `${num(p.charCount ?? 0)} ${t("characters")}`
                    : eta != null
                      ? `~${num(eta)}s`
                      : p.status}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function StatusDot({ status }: { status: EngineProgress["status"] }) {
  const color =
    status === "succeeded" ? "#22c55e"
    : status === "failed" || status === "timeout" ? "#ef4444"
    : status === "running" ? "var(--accent)"
    : "var(--muted)";
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${status === "running" ? "animate-pulse" : ""}`}
      style={{ background: color }}
      aria-label={status}
    />
  );
}
