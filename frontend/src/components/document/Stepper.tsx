"use client";

import { Check } from "lucide-react";
import { StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useLocale } from "../Providers";

export type Step = "extract" | "review" | "columns" | "details" | "chat";
type StepDef = { id: Step; label: StringKey };
export const STEPS: StepDef[] = [
  { id: "extract", label: "stepExtract" },
  { id: "review", label: "stepReview" },
  { id: "details", label: "stepDetails" },
  { id: "chat", label: "stepChat" },
];
// A spreadsheet is never OCR'd: its columns are reviewed instead of its text.
export const SHEET_STEPS: StepDef[] = [
  { id: "columns", label: "stepColumns" },
  { id: "details", label: "stepDetails" },
  { id: "chat", label: "stepChat" },
];

const FINALIZED = ["text_finalized", "enriching", "enriched", "indexing", "indexed", "ready"];
const HAS_DETAILS = ["enriched", "indexing", "indexed", "ready"];
export const CHAT_READY = ["indexed", "ready"];

/** Which steps are done, derived from the document's lifecycle state. */
export function stepProgress(status: string, revisionNo: number | null): Record<Step, boolean> {
  return {
    extract: revisionNo != null,
    review: FINALIZED.includes(status),
    columns: FINALIZED.includes(status),
    details: HAS_DETAILS.includes(status),
    chat: CHAT_READY.includes(status),
  };
}

/** Where a document should open: the first unfinished step, or Review when done. */
export function initialStep(status: string, revisionNo: number | null, spreadsheet = false): Step {
  const done = stepProgress(status, revisionNo);
  if (spreadsheet) return done.columns && !done.details ? "details" : "columns";
  if (!done.extract) return "extract";
  if (!done.review) return "review";
  if (!done.details) return "details";
  return "review";
}

export function Stepper({
  current, done, onSelect, steps = STEPS,
}: {
  current: Step;
  done: Record<Step, boolean>;
  onSelect: (step: Step) => void;
  steps?: StepDef[];
}) {
  const { t } = useLocale();
  const STEPS = steps;
  // A step is reachable once every step before it is done (or it is itself done).
  const reachable = (index: number) => STEPS.slice(0, index).every((s) => done[s.id]);

  return (
    <nav aria-label={t("progressSteps")} className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <ol className="flex min-w-max items-center gap-1 sm:gap-2">
        {STEPS.map((step, index) => {
          const active = step.id === current;
          const complete = done[step.id];
          const enabled = reachable(index) || complete;
          return (
            <li key={step.id} className="flex items-center gap-1 sm:gap-2">
              {index > 0 && (
                <span className={cn("h-px w-4 sm:w-8", done[STEPS[index - 1].id] ? "bg-primary/50" : "bg-border")} aria-hidden />
              )}
              <button
                type="button"
                onClick={() => enabled && onSelect(step.id)}
                disabled={!enabled}
                aria-current={active ? "step" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-full py-1.5 pe-3 ps-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed",
                  active ? "bg-primary/10 font-medium text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50 disabled:hover:bg-transparent",
                )}
              >
                <span
                  className={cn(
                    "grid size-6 place-items-center rounded-full border text-xs font-semibold",
                    complete
                      ? "border-primary bg-primary text-primary-foreground"
                      : active
                        ? "border-primary text-primary"
                        : "border-border",
                  )}
                >
                  {complete ? <Check className="size-3.5" strokeWidth={3} /> : <span className="tabular-nums">{index + 1}</span>}
                </span>
                {t(step.label)}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
