"use client";

import { DocStatus } from "@/lib/api";

const COLORS: Record<string, string> = {
  ready: "#22c55e",
  indexed: "#22c55e",
  failed: "#ef4444",
  ocr_failed: "#ef4444",
  ocr_running: "#3b82f6",
  preprocessing: "#3b82f6",
  enriching: "#3b82f6",
  indexing: "#3b82f6",
  ocr_partial: "#f59e0b",
};

const BUSY = new Set([
  "preprocessing", "ocr_running", "ocr_partial", "enriching", "indexing",
]);

export function StatusBadge({ status }: { status: DocStatus | string }) {
  const color = COLORS[status] ?? "#6b7280";
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={{ background: `${color}1a`, color }}
    >
      {BUSY.has(status) && (
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full" style={{ background: color }} />
      )}
      {status.replace(/_/g, " ")}
    </span>
  );
}
