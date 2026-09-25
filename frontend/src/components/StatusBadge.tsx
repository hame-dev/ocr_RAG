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
export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const { t } = useLocale();
  const meta = statusMeta(status);
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
