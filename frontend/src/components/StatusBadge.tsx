"use client";

import { Badge } from "@/components/ui/badge";
import { statusMeta, Tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLocale } from "./Providers";

const VARIANT: Record<Tone, "secondary" | "default" | "success" | "warning" | "destructive"> = {
  neutral: "secondary",
  info: "default",
  success: "success",
  warning: "warning",
  destructive: "destructive",
};

/** A document's lifecycle state in words, never the raw `ocr_running`. */
export function StatusBadge({
  status, className, spreadsheet,
}: { status: string; className?: string; spreadsheet?: boolean }) {
  const { t } = useLocale();
  const base = statusMeta(status);
  // A spreadsheet is never OCR'd: at "preprocessed" it waits for its columns.
  const meta = spreadsheet && status === "preprocessed" ? { ...base, label: "statusReviewColumns" as const } : base;
  return (
    <Badge variant={VARIANT[meta.tone]} className={className}>
      <span
        className={cn("size-1.5 rounded-full bg-current", meta.busy && "animate-pulse")}
        aria-hidden
      />
      {meta.label ? t(meta.label) : status.replace(/_/g, " ")}
    </Badge>
  );
}
