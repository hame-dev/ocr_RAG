import type { DocStatus } from "./api";
import { Locale, StringKey, fmt, num } from "./i18n";

type Translate = (key: StringKey) => string;

/** "3 minutes ago" / "منذ 3 دقائق", with Latin digits in both locales. */
export function relativeTime(iso: string, locale: Locale): string {
  const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000], ["month", 2_592_000], ["week", 604_800],
    ["day", 86_400], ["hour", 3_600], ["minute", 60],
  ];
  const format = new Intl.RelativeTimeFormat(locale === "ar" ? "ar-u-nu-latn" : "en", {
    numeric: "auto",
  });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(seconds, "second");
}

export function bytes(size: number): string {
  if (size < 1024) return `${num(size)} B`;
  if (size < 1024 ** 2) return `${num(size / 1024, 0)} KB`;
  return `${num(size / 1024 ** 2, 1)} MB`;
}

export function duration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${num(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${num(minutes)}m ${num(seconds % 60)}s`;
}

export function pages(n: number, t: Translate): string {
  return fmt(t(n === 1 ? "pagesOne" : "pagesOther"), { n: num(n) });
}

/** annual_rent_amount → "Annual rent amount". */
export function humanizeKey(key: string): string {
  const words = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
  return words.charAt(0).toUpperCase() + words.slice(1).toLowerCase();
}

export type Tone = "neutral" | "info" | "success" | "warning" | "destructive";

const STATUS: Record<DocStatus, { label: StringKey; tone: Tone; busy?: boolean }> = {
  uploaded: { label: "statusUploaded", tone: "neutral" },
  preprocessing: { label: "statusPreprocessing", tone: "info", busy: true },
  preprocessed: { label: "statusPreprocessed", tone: "neutral" },
  ocr_running: { label: "statusOcrRunning", tone: "info", busy: true },
  ocr_partial: { label: "statusOcrPartial", tone: "info", busy: true },
  ocr_done: { label: "statusOcrDone", tone: "neutral" },
  ocr_failed: { label: "statusOcrFailed", tone: "destructive" },
  text_finalized: { label: "statusTextFinalized", tone: "info" },
  enriching: { label: "statusEnriching", tone: "info", busy: true },
  enriched: { label: "statusEnriched", tone: "info" },
  indexing: { label: "statusIndexing", tone: "info", busy: true },
  indexed: { label: "statusIndexed", tone: "success" },
  ready: { label: "statusReady", tone: "success" },
  failed: { label: "statusFailed", tone: "destructive" },
};

export function statusMeta(status: string) {
  return STATUS[status as DocStatus] ?? { label: null, tone: "neutral" as Tone, busy: false };
}

/** Library filter buckets. */
export function statusBucket(status: string): "processing" | "ready" | "attention" | "other" {
  const meta = statusMeta(status);
  if (meta.tone === "destructive") return "attention";
  if (meta.tone === "success") return "ready";
  if (meta.busy) return "processing";
  // Waiting on the user (pick an OCR result, finalize the text).
  if (["preprocessed", "ocr_done", "uploaded"].includes(status)) return "attention";
  return "processing";
}
