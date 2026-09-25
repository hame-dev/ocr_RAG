"use client";

import { memo, useState } from "react";
import { Check, ChevronDown, ListChecks, Loader2, Search } from "lucide-react";
import { PhaseSummary } from "@/lib/api";
import { fmt, num } from "@/lib/i18n";
import type { PhaseEvent } from "@/lib/sse";
import { cn } from "@/lib/utils";
import { useLocale } from "../Providers";

interface Row {
  key: string;
  label: string;
  detail?: string;
  sub?: string[];
  search?: boolean;
}

/**
 * The deep think / deep research timeline: each step appears as it starts,
 * with a spinner on the active one. Collapses to a one-line summary when done.
 */
export const ResearchProgress = memo(function ResearchProgress({
  phases, summary, live,
}: {
  phases: PhaseEvent[];
  summary?: PhaseSummary | null;
  live: boolean;
}) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);

  const rows = phases.length ? rowsFromEvents(phases, t) : rowsFromSummary(summary, t);
  if (!rows.length) return null;

  const queries = summary?.queries.length ?? phases.filter((p) => p.phase === "searching").length;
  const sources = summary?.sources ?? phases.find((p) => p.phase === "writing")?.sources ?? 0;
  const steps = summary?.steps.length ?? phases.find((p) => p.phase === "planned")?.total ?? 0;
  const research = queries > 0;
  const headline = research
    ? fmt(t(queries === 1 ? "researchSummaryOne" : "researchSummary"), { q: num(queries), s: num(sources ?? 0) })
    : fmt(t(steps === 1 ? "thinkSummaryOne" : "thinkSummary"), { n: num(steps) });

  const list = (
    <ol className="space-y-1.5">
      {rows.map((row, index) => {
        const active = live && index === rows.length - 1;
        return (
          <li key={row.key} className="flex gap-2.5 text-[13px]">
            <span className={cn(
              "mt-0.5 grid size-4 shrink-0 place-items-center rounded-full",
              active ? "text-primary" : "bg-primary/10 text-primary",
            )}>
              {active ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3" strokeWidth={3} />}
            </span>
            <div className="min-w-0">
              <p className={cn(active ? "font-medium text-foreground" : "text-muted-foreground")}>
                {row.label}
                {row.detail && (
                  <span className={cn("ms-1.5", row.search && "inline-flex items-center gap-1 rounded bg-muted px-1.5 py-px text-xs")} dir="auto">
                    {row.search && <Search className="size-3" />}
                    {row.detail}
                  </span>
                )}
              </p>
              {row.sub && row.sub.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {row.sub.map((item, i) => (
                    <li key={i} className="flex gap-1.5" dir="auto">
                      <span className="tabular-nums">{num(i + 1)}.</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );

  if (live) return <div className="rounded-xl border bg-card/50 p-3">{list}</div>;

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex items-center gap-2 py-0.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ListChecks className="size-4" />
        <span className="font-medium">{headline}</span>
        <ChevronDown className={cn("size-3.5 transition-transform duration-200", open && "rotate-180")} />
      </button>
      <div className="disclosure" data-open={open}>
        <div><div className="mt-2 rounded-xl border bg-card/50 p-3">{list}</div></div>
      </div>
    </div>
  );
});

type Translate = ReturnType<typeof useLocale>["t"];

function rowsFromEvents(phases: PhaseEvent[], t: Translate): Row[] {
  const rows: Row[] = [];
  phases.forEach((p, i) => {
    const key = `${p.phase}-${i}`;
    switch (p.phase) {
      case "understanding":
        rows.push({ key, label: t("phaseUnderstanding") });
        break;
      case "planning":
        rows.push({ key, label: t("phasePlanning") });
        break;
      case "planned": {
        // Fold the plan into the preceding "planning"/"understanding" row.
        const last = rows[rows.length - 1];
        if (last) last.sub = p.steps;
        else rows.push({ key, label: t("phasePlanning"), sub: p.steps });
        break;
      }
      case "working":
        rows.push({
          key,
          label: fmt(t("phaseWorking"), { i: num(p.index ?? 0), n: num(p.total ?? 0) }),
          detail: p.detail,
        });
        break;
      case "searching":
        rows.push({ key, label: t("phaseSearching"), detail: p.detail, search: true });
        break;
      case "analyzing":
        rows.push({ key, label: t("phaseAnalyzing") });
        break;
      case "analyzed": {
        const last = rows[rows.length - 1];
        const text = fmt(t("phaseAnswered"), { a: num(p.answered ?? 0), n: num(p.total ?? 0) });
        if (last) last.detail = text;
        break;
      }
      case "reviewing":
        rows.push({ key, label: t("phaseReviewing") });
        break;
      case "writing":
        rows.push({ key, label: t("phaseWriting") });
        break;
    }
  });
  return rows;
}

function rowsFromSummary(summary: PhaseSummary | null | undefined, t: Translate): Row[] {
  if (!summary) return [];
  const rows: Row[] = [];
  if (summary.steps.length) rows.push({ key: "plan", label: t("phasePlanning"), sub: summary.steps });
  summary.queries.forEach((query, i) => rows.push({ key: `q${i}`, label: t("phaseSearching"), detail: query, search: true }));
  rows.push({ key: "write", label: t("phaseWriting") });
  return rows;
}
