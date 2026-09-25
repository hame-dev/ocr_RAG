"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Comparison, getComparison } from "@/lib/api";
import { subscribeToDocument } from "@/lib/sse";

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
 * One SSE subscription per document page. Every stage — preprocessing, each
 * engine's per-page progress, enrichment, indexing — arrives here and is
 * reduced into state the step components render.
 */
export function useDocumentStream(
  id: string,
  { onStatus, onMetadata }: { onStatus: (to: string) => void; onMetadata: () => void },
) {
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<string, EngineProgress>>({});
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const batchRef = useRef<string | null>(null);
  // Callbacks change identity every render; the subscription must not.
  const handlers = useRef({ onStatus, onMetadata });
  handlers.current = { onStatus, onMetadata };

  const loadComparison = useCallback(async (batchId: string) => {
    try {
      setComparison(await getComparison(batchId));
    } catch {
      /* not ready yet */
    }
  }, []);

  useEffect(() => {
    if (!id) return;
    return subscribeToDocument(id, {
      snapshot: (data) => {
        setLiveStatus(data.status);
        const latest = data.batches?.[0];
        if (!latest) return;
        batchRef.current = latest.batch_id;
        const next: Record<string, EngineProgress> = {};
        for (const run of latest.runs ?? []) {
          next[run.engine] = {
            engine: run.engine,
            status: run.status,
            page: run.status === "succeeded" ? data.page_count ?? 1 : 0,
            of: data.page_count ?? 1,
            durationMs: run.duration_ms,
            charCount: run.char_count,
            error: run.error_message,
          };
        }
        setProgress(next);
        if (latest.runs?.some((r: { status: string }) => r.status === "succeeded")) {
          void loadComparison(latest.batch_id);
        }
      },
      status_change: (data) => {
        setLiveStatus(data.to);
        handlers.current.onStatus(data.to);
      },
      ocr_run_started: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: { ...(p[data.engine] ?? { engine: data.engine, page: 0, of: 1 }), status: "running" },
        }));
      },
      // Per-page events are what make a slow VLM visibly advance.
      ocr_page_done: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: {
            ...(p[data.engine] ?? { engine: data.engine }),
            engine: data.engine, status: "running", page: data.page, of: data.of,
          },
        }));
      },
      ocr_run_finished: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: {
            ...(p[data.engine] ?? { engine: data.engine, page: 0, of: 1 }),
            status: data.status,
            page: data.status === "succeeded" ? (p[data.engine]?.of ?? 1) : p[data.engine]?.page ?? 0,
            durationMs: data.duration_ms,
            charCount: data.char_count,
            error: data.error_message,
          },
        }));
        // Refresh the results as EACH engine lands, not once at the end.
        if (batchRef.current) void loadComparison(batchRef.current);
      },
      batch_status: (data) => {
        batchRef.current = data.batch_id;
        if (data.final) void loadComparison(data.batch_id);
      },
      extraction_plan_ready: () => handlers.current.onMetadata(),
      enrichment_done: () => handlers.current.onMetadata(),
      indexed: () => handlers.current.onStatus("indexed"),
    });
  }, [id, loadComparison]);

  /** Called when the user starts a new batch, before the first event arrives. */
  const beginBatch = useCallback((batchId: string, engines: string[], pageCount: number) => {
    batchRef.current = batchId;
    setComparison(null);
    setProgress(
      Object.fromEntries(
        engines.map((engine) => [engine, { engine, status: "queued" as const, page: 0, of: pageCount }]),
      ),
    );
  }, []);

  const pending = Object.values(progress)
    .filter((p) => p.status === "queued" || p.status === "running")
    .map((p) => p.engine);

  return { liveStatus, progress, comparison, pending, beginBatch };
}
