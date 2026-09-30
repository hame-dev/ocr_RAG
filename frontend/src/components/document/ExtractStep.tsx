"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle, Check, ChevronDown, ChevronLeft, ChevronRight, Clock, Loader2, Play, Trophy,
} from "lucide-react";
import { Comparison, ComparisonColumn, Engine } from "@/lib/api";
import { duration, pages as pagesLabel } from "@/lib/format";
import { fmt, num, StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Hint } from "@/components/ui/tooltip";
import { useLocale } from "../Providers";
import { EngineProgress } from "./useDocumentStream";

const TIER: Record<Engine["tier"], StringKey> = {
  native: "tierNative",
  classical: "tierClassical",
  neural: "tierNeural",
  vlm: "tierVlm",
};

export function ExtractStep({
  engines, defaultEngine, pageCount, isDigital, preparing, progress, pending, comparison,
  onRun, onUseResult,
}: {
  engines: Engine[];
  defaultEngine: string | null;
  pageCount: number;
  isDigital: boolean;
  preparing: boolean;
  progress: Record<string, EngineProgress>;
  pending: string[];
  comparison: Comparison | null;
  onRun: (engines: string[]) => Promise<void>;
  onUseResult: (runId: string) => Promise<void>;
}) {
  return (
    <div className="space-y-6">
      <EnginePicker
        engines={engines}
        defaultEngine={defaultEngine}
        pageCount={pageCount}
        isDigital={isDigital}
        disabled={preparing || pending.length > 0}
        running={pending.length > 0}
        onRun={onRun}
      />
      <RunProgress progress={progress} engines={engines} />
      <Results comparison={comparison} pending={pending} engines={engines} onUseResult={onUseResult} />
    </div>
  );
}

function engineName(engines: Engine[], name: string, locale: "ar" | "en") {
  const engine = engines.find((e) => e.name === name);
  if (!engine) return name;
  return locale === "ar" ? engine.display_name_ar : engine.display_name_en;
}

function EnginePicker({
  engines, defaultEngine, pageCount, isDigital, disabled, running, onRun,
}: {
  engines: Engine[];
  defaultEngine: string | null;
  pageCount: number;
  isDigital: boolean;
  disabled: boolean;
  running: boolean;
  onRun: (engines: string[]) => Promise<void>;
}) {
  const { t, locale } = useLocale();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [showUnavailable, setShowUnavailable] = useState(false);
  const [starting, setStarting] = useState(false);

  // The embedded text layer only helps when the PDF actually has one.
  const relevant = engines.filter((e) => e.name !== "native_pdf" || isDigital);
  const recommended = new Set([defaultEngine, isDigital ? "native_pdf" : null].filter(Boolean));
  const available = relevant
    .filter((e) => e.available)
    .sort((a, b) =>
      Number(recommended.has(b.name)) - Number(recommended.has(a.name))
      || a.est_seconds_per_page - b.est_seconds_per_page);
  const unavailable = relevant.filter((e) => !e.available);

  // Until the user touches the selection, preselect the recommended engines.
  const chosen = selected ?? available.filter((e) => recommended.has(e.name)).map((e) => e.name);
  const toggle = (name: string) =>
    setSelected(chosen.includes(name) ? chosen.filter((n) => n !== name) : [...chosen, name]);

  const estimate = chosen.reduce(
    (total, name) => total + (engines.find((e) => e.name === name)?.est_seconds_per_page ?? 0) * pageCount,
    0,
  );

  async function run() {
    setStarting(true);
    try {
      await onRun(chosen);
    } finally {
      setStarting(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start gap-3 space-y-0">
        <div className="min-w-0 flex-1 basis-60">
          <CardTitle className="text-base">{t("selectEngines")}</CardTitle>
          <CardDescription className="mt-1.5">{t("engineHint")}</CardDescription>
        </div>
        <div className="flex items-center gap-3">
          {chosen.length > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="size-3.5" />
              {fmt(t("estimatedTotal"), {
                time: duration(estimate * 1000),
                pages: pagesLabel(pageCount, t),
              })}
            </span>
          )}
          <Button onClick={() => void run()} disabled={!chosen.length || disabled || starting}>
            {starting || running ? <Loader2 className="animate-spin" /> : <Play className="rtl:-scale-x-100" />}
            {t("runOcr")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {available.map((engine) => {
            const isSelected = chosen.includes(engine.name);
            return (
              <button
                key={engine.name}
                type="button"
                role="checkbox"
                aria-checked={isSelected}
                onClick={() => toggle(engine.name)}
                disabled={disabled}
                className={cn(
                  "group relative flex flex-col rounded-lg border p-3 text-start transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60",
                  isSelected ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:border-foreground/20 hover:bg-accent/40",
                )}
              >
                <div className="flex items-center gap-2 pe-6">
                  <span className="truncate text-sm font-medium">
                    {locale === "ar" ? engine.display_name_ar : engine.display_name_en}
                  </span>
                  {recommended.has(engine.name) && <Badge className="px-1.5 py-0 text-[10px]">{t("recommended")}</Badge>}
                </div>
                <span
                  className={cn(
                    "absolute end-3 top-3 grid size-4 place-items-center rounded border",
                    isSelected ? "border-primary bg-primary text-primary-foreground" : "border-input",
                  )}
                  aria-hidden
                >
                  {isSelected && <Check className="size-3" strokeWidth={3} />}
                </span>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {locale === "ar" ? engine.description_ar : engine.description_en}
                </p>
                <div className="mt-2.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="rounded bg-muted px-1.5 py-0.5">{t(TIER[engine.tier])}</span>
                  <span className="tabular-nums">{fmt(t("perPage"), { n: num(engine.est_seconds_per_page) })}</span>
                </div>
              </button>
            );
          })}
        </div>

        {unavailable.length > 0 && (
          <div>
            <Button
              variant="ghost"
              size="sm"
              className="-ms-2 text-muted-foreground"
              onClick={() => setShowUnavailable((value) => !value)}
              aria-expanded={showUnavailable}
            >
              <ChevronDown className={cn("transition-transform", showUnavailable && "rotate-180")} />
              {showUnavailable ? t("hideUnavailable") : fmt(t("showUnavailable"), { n: unavailable.length })}
            </Button>
            {showUnavailable && (
              <ul className="mt-2 divide-y rounded-lg border">
                {unavailable.map((engine) => (
                  <li key={engine.name} className="flex flex-col gap-1 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3">
                    <span className="text-sm font-medium text-muted-foreground sm:w-48 sm:shrink-0">
                      {locale === "ar" ? engine.display_name_ar : engine.display_name_en}
                    </span>
                    <code className="min-w-0 truncate text-xs text-muted-foreground" dir="ltr" title={engine.detail}>
                      {engine.detail}
                    </code>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RunProgress({ progress, engines }: { progress: Record<string, EngineProgress>; engines: Engine[] }) {
  const { t, locale } = useLocale();
  const entries = Object.values(progress);
  const active = entries.some((e) => e.status === "queued" || e.status === "running");
  if (!entries.length || !active) return null;

  const statusLabel: Record<string, StringKey> = {
    queued: "queued", running: "running", succeeded: "succeeded", failed: "failed", timeout: "failed",
    skipped: "skipped", cancelled: "cancelled",
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Loader2 className="size-4 animate-spin text-primary" />
          {t("runningEngines")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-3 sm:grid-cols-2">
          {entries.map((p) => {
            const meta = engines.find((e) => e.name === p.engine);
            const failed = p.status === "failed" || p.status === "timeout";
            const pct = p.status === "succeeded" ? 100 : p.of > 0 ? (p.page / p.of) * 100 : 0;
            return (
              <li key={p.engine} className="rounded-lg border p-3">
                <div className="flex items-center gap-2 text-sm">
                  <span className="truncate font-medium">{engineName(engines, p.engine, locale)}</span>
                  <Badge
                    variant={failed ? "destructive" : p.status === "succeeded" ? "success" : "secondary"}
                    className="ms-auto"
                  >
                    {t(statusLabel[p.status] ?? "running")}
                  </Badge>
                </div>
                <Progress
                  // A sliver while the first page is still in flight, so a slow
                  // engine never looks like it has not started.
                  value={p.status === "running" && pct === 0 ? 6 : pct}
                  className="mt-2.5"
                  indicatorClassName={cn(failed && "bg-destructive", p.status === "running" && pct === 0 && "animate-pulse")}
                />
                <p className="mt-1.5 truncate text-xs text-muted-foreground">
                  {failed
                    ? p.error ?? t("failed")
                    : p.status === "succeeded"
                      ? `${num(p.charCount ?? 0)} ${t("characters")}`
                      : meta && p.of > 0
                        ? `${fmt(t("pageOf"), { page: num(p.page), of: num(p.of) })} · ~${duration((p.of - p.page) * meta.est_seconds_per_page * 1000)}`
                        : t("queued")}
                </p>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function Results({
  comparison, pending, engines, onUseResult,
}: {
  comparison: Comparison | null;
  pending: string[];
  engines: Engine[];
  onUseResult: (runId: string) => Promise<void>;
}) {
  const { t, locale } = useLocale();
  const [pageIndex, setPageIndex] = useState(0);
  const pages = comparison?.pages ?? [];
  const page = pages[Math.min(pageIndex, Math.max(pages.length - 1, 0))];

  // Best-ranked first, so the recommended choice is always the first card.
  const columns = useMemo(() => {
    if (!page) return [];
    const rank = comparison?.ranking ?? [];
    return [...page.columns].sort((a, b) => {
      const ra = rank.indexOf(a.engine), rb = rank.indexOf(b.engine);
      return (ra < 0 ? 99 : ra) - (rb < 0 ? 99 : rb);
    });
  }, [page, comparison?.ranking]);

  if (!page && !pending.length) return null;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-3 space-y-0 pb-4">
        <div className="min-w-0 flex-1 basis-60">
          <CardTitle className="text-base">{t("results")}</CardTitle>
          <CardDescription className="mt-1.5">{t("resultsHint")}</CardDescription>
        </div>
        {pages.length > 1 && (
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon-sm" disabled={pageIndex === 0}
                    onClick={() => setPageIndex((i) => i - 1)} aria-label={t("previousPage")}>
              <ChevronLeft className="rtl:-scale-x-100" />
            </Button>
            <span className="tabular-nums px-2 text-xs text-muted-foreground">
              {fmt(t("pageOf"), { page: num(pageIndex + 1), of: num(pages.length) })}
            </span>
            <Button variant="outline" size="icon-sm" disabled={pageIndex >= pages.length - 1}
                    onClick={() => setPageIndex((i) => i + 1)} aria-label={t("nextPage")}>
              <ChevronRight className="rtl:-scale-x-100" />
            </Button>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 lg:grid-cols-2">
          {columns.map((column) => (
            <ResultCard
              key={column.run_id}
              column={column}
              name={engineName(engines, column.engine, locale)}
              best={column.run_id === comparison?.baseline_run_id}
              locked={page?.consensus.locked_spans ?? []}
              onUse={() => onUseResult(column.run_id)}
            />
          ))}
          {pending.map((engine) => (
            <div key={engine} className="space-y-3 rounded-lg border p-4">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                {engineName(engines, engine, locale)}
              </div>
              {[92, 78, 85, 64, 88].map((width, i) => (
                <Skeleton key={i} className="h-3" style={{ width: `${width}%` }} />
              ))}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ResultCard({
  column, name, best, locked, onUse,
}: {
  column: ComparisonColumn;
  name: string;
  best: boolean;
  locked: number[][];
  onUse: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [busy, setBusy] = useState(false);
  const suspect = column.warnings?.includes("suspected_summarization");

  return (
    <div className={cn("flex flex-col rounded-lg border", best && "border-primary/50 ring-1 ring-primary/30")}>
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <span className="text-sm font-medium">{name}</span>
        {best && <Badge><Trophy /> {t("bestMatch")}</Badge>}
        <div className="ms-auto flex items-center gap-3 text-xs text-muted-foreground">
          {column.confidence != null && (
            <Hint label={t("confidence")}>
              <span className="tabular-nums">{num(column.confidence * 100)}%</span>
            </Hint>
          )}
          <span className="tabular-nums">{num(column.char_count)} {t("characters")}</span>
          {column.duration_ms != null && <span className="tabular-nums">{duration(column.duration_ms)}</span>}
        </div>
      </div>

      {suspect && (
        <p className="flex items-start gap-2 border-b bg-warning/10 px-4 py-2 text-xs text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          {t("suspectSummary")}
        </p>
      )}

      <div className="doc-text scrollbar-thin max-h-80 flex-1 overflow-y-auto px-4 py-3 text-sm" dir="auto">
        <ConsensusText text={column.text} locked={locked} />
      </div>

      <div className="border-t p-3">
        <Button
          className="w-full"
          variant={best ? "default" : "outline"}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try { await onUse(); } finally { setBusy(false); }
          }}
        >
          {busy && <Loader2 className="animate-spin" />}
          {t("selectThis")}
        </Button>
      </div>
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
    parts.push(<span key={`a${start}`} className="rounded-sm bg-success/10">{text.slice(start, end)}</span>);
    cursor = end;
  }
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}
