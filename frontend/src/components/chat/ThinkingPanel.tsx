"use client";

import { memo, useEffect, useRef, useState } from "react";
import { Brain, ChevronDown } from "lucide-react";
import { fmt, num } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Markdown } from "../Markdown";
import { useLocale } from "../Providers";

/**
 * The model's reasoning, like Claude's / ChatGPT's thinking disclosure.
 *
 * While thinking: a shimmering "Thinking…" with a live timer and a faded
 * preview of the latest lines. Once the answer starts it collapses to
 * "Thought for 12s", expandable to the full trail.
 */
export const ThinkingPanel = memo(function ThinkingPanel({
  reasoning, live, thinkingMs, startedAt,
}: {
  reasoning: string;
  live: boolean;
  thinkingMs?: number | null;
  startedAt?: number | null;
}) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const previewRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  // Keep the live preview pinned to the newest reasoning.
  useEffect(() => {
    const element = previewRef.current;
    if (live && element) element.scrollTop = element.scrollHeight;
  }, [reasoning, live]);

  const seconds = live
    ? Math.max(0, Math.round((now - (startedAt ?? now)) / 1000))
    : Math.max(1, Math.round((thinkingMs ?? 0) / 1000));

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex items-center gap-2 rounded-md py-0.5 text-muted-foreground transition-colors hover:text-foreground"
      >
        <Brain className={cn("size-4", live && "text-primary")} />
        {live ? (
          <span className="shimmer-text animate-shimmer font-medium">{t("thinkingLive")}</span>
        ) : (
          <span className="font-medium">{fmt(t("thoughtFor"), { s: num(seconds) })}</span>
        )}
        {live && <span className="tabular-nums text-xs">{num(seconds)}s</span>}
        <ChevronDown className={cn("size-3.5 transition-transform duration-200", open && "rotate-180")} />
      </button>

      {live && !open && reasoning && (
        <div
          ref={previewRef}
          aria-hidden
          className="mt-1.5 max-h-20 overflow-hidden border-s-2 ps-3 text-xs leading-relaxed text-muted-foreground [mask-image:linear-gradient(to_bottom,transparent,black_45%)]"
          dir="auto"
        >
          <p className="whitespace-pre-wrap">{reasoning.slice(-900)}</p>
        </div>
      )}

      <div className="disclosure" data-open={open}>
        <div>
          <div className="scrollbar-thin mt-2 max-h-96 overflow-y-auto border-s-2 ps-3 text-muted-foreground [&_.md]:text-[13px]">
            <Markdown>{reasoning || "…"}</Markdown>
          </div>
        </div>
      </div>
    </div>
  );
});
