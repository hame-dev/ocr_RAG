"use client";

import { memo, useState } from "react";
import { AlertCircle, ChevronDown, SquareTerminal, Table2 } from "lucide-react";
import { CodeRun } from "@/lib/api";
import { duration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLocale } from "../Providers";

/**
 * One General-mode code run, collapsed to "Ran code · 1s" like the thinking
 * disclosure; expands to the Python and what it printed. A spreadsheet query
 * (Documents mode) uses the same panel: the SQL and its result table.
 */
export const CodeRunPanel = memo(function CodeRunPanel({ run }: { run: CodeRun }) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const query = run.name === "query_spreadsheet";
  const Icon = !run.ok ? AlertCircle : query ? Table2 : SquareTerminal;
  const title = query ? (run.ok ? t("ranQuery") : t("ranQueryFailed")) : run.ok ? t("ranCode") : t("ranCodeFailed");

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          "flex items-center gap-2 rounded-md py-0.5 text-muted-foreground transition-colors hover:text-foreground",
          !run.ok && "text-destructive hover:text-destructive",
        )}
      >
        <Icon className="size-4" />
        <span className="font-medium">{title}</span>
        {run.duration_ms != null && (
          // ltr: "<" is a mirrored character and would read ">1s" in Arabic.
          <span className="tabular-nums text-xs" dir="ltr">
            {run.duration_ms < 1000 ? "<1s" : duration(run.duration_ms)}
          </span>
        )}
        <ChevronDown className={cn("size-3.5 transition-transform duration-200", open && "rotate-180")} />
      </button>

      <div className="disclosure" data-open={open}>
        <div>
          <div className="mt-2 overflow-hidden rounded-lg border bg-muted/40" dir="ltr">
            <pre className="scrollbar-thin max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed">
              <code>{run.code}</code>
            </pre>
            {run.stdout && (
              <Output label={query ? t("queryResult") : t("codeOutput")} text={run.stdout} />
            )}
            {run.stderr && (
              <Output label={t("codeError")} text={run.stderr} error={!run.ok} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
});

function Output({ label, text, error }: { label: string; text: string; error?: boolean }) {
  return (
    <div className="border-t">
      <p className="px-3 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <pre
        className={cn(
          "scrollbar-thin max-h-60 overflow-auto whitespace-pre-wrap px-3 pb-3 pt-1 font-mono text-xs",
          error && "text-destructive",
        )}
      >
        {text}
      </pre>
    </div>
  );
}
