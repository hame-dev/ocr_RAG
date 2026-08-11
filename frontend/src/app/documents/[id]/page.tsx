"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createConversation, enrich, finalizeText, getComparison, getDocument,
  getExtractionPlan, getMetadata, listEngines, saveRevision, selectRun, startOcr,
} from "@/lib/api";
import { subscribeToDocument } from "@/lib/sse";
import { useLocale } from "@/components/Providers";
import { EngineProgress, OcrProgressPanel } from "@/components/OcrProgressPanel";
import { OcrComparisonGrid } from "@/components/OcrComparisonGrid";
import { EngineSelector } from "@/components/EngineSelector";
import { TextEditor } from "@/components/TextEditor";
import { MetadataPanel } from "@/components/MetadataPanel";
import { StatusBadge } from "@/components/StatusBadge";

export default function DocumentPage() {
  const { t } = useLocale();
  const router = useRouter();
  const { id } = useParams<{ id: string }>();

  const [snapshot, setSnapshot] = useState<any>(null);
  const [progress, setProgress] = useState<Record<string, EngineProgress>>({});
  const [batchId, setBatchId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [comparison, setComparison] = useState<any>(null);
  const [text, setText] = useState<string>("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const batchRef = useRef<string | null>(null);
  const defaultEngineAppliedRef = useRef(false);

  const { data: doc, refetch: refetchDoc } = useQuery({
    queryKey: ["document", id],
    queryFn: () => getDocument(id),
  });
  const { data: engineData } = useQuery({ queryKey: ["engines"], queryFn: () => listEngines() });
  const engines = engineData?.engines ?? [];

  useEffect(() => {
    if (!engineData || defaultEngineAppliedRef.current) return;
    defaultEngineAppliedRef.current = true;
    const defaultEngine = engineData.engines.find(
      (engine) => engine.name === engineData.default_engine && engine.available,
    );
    if (defaultEngine) setSelected([defaultEngine.name]);
  }, [engineData]);

  const { data: metadata, refetch: refetchMetadata } = useQuery({
    queryKey: ["metadata", id],
    queryFn: () => getMetadata(id).catch(() => null),
  });
  const { data: plan, refetch: refetchPlan } = useQuery({
    queryKey: ["plan", id],
    queryFn: () => getExtractionPlan(id).catch(() => null),
  });

  const loadComparison = useCallback(async (bid: string) => {
    try {
      setComparison(await getComparison(bid));
    } catch {
      /* not ready yet */
    }
  }, []);

  // One SSE subscription for the whole page. Every stage — preprocessing, each
  // engine's per-page progress, enrichment, indexing — arrives here.
  useEffect(() => {
    if (!id) return;
    return subscribeToDocument(id, {
      snapshot: (data) => {
        setSnapshot(data);
        const latest = data.batches?.[0];
        if (latest) {
          batchRef.current = latest.batch_id;
          setBatchId(latest.batch_id);
          const next: Record<string, EngineProgress> = {};
          for (const run of latest.runs ?? []) {
            next[run.engine] = {
              engine: run.engine,
              status: run.status,
              page: run.status === "succeeded" ? 1 : 0,
              of: data.page_count ?? 1,
              durationMs: run.duration_ms,
              charCount: run.char_count,
              error: run.error_message,
            };
          }
          setProgress(next);
          if (latest.runs?.some((r: any) => r.status === "succeeded")) {
            void loadComparison(latest.batch_id);
          }
        }
      },
      status_change: (data) => {
        setSnapshot((s: any) => ({ ...s, status: data.to }));
        void refetchDoc();
        if (["enriched", "ready", "indexed"].includes(data.to)) {
          void refetchMetadata();
          void refetchPlan();
        }
      },
      ocr_run_started: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: { ...(p[data.engine] ?? { engine: data.engine, page: 0, of: 1 }),
                           engine: data.engine, status: "running" },
        }));
      },
      // This is what makes the loading indicator advance instead of hanging.
      ocr_page_done: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: {
            ...(p[data.engine] ?? { engine: data.engine, status: "running" }),
            engine: data.engine, status: "running", page: data.page, of: data.of,
          },
        }));
      },
      ocr_run_finished: (data) => {
        setProgress((p) => ({
          ...p,
          [data.engine]: {
            ...(p[data.engine] ?? { engine: data.engine, page: 0, of: 1 }),
            engine: data.engine, status: data.status,
            durationMs: data.duration_ms, charCount: data.char_count,
            error: data.error_message,
          },
        }));
        // Refresh the grid as EACH engine lands, not once at the end.
        if (batchRef.current) void loadComparison(batchRef.current);
      },
      batch_status: (data) => {
        batchRef.current = data.batch_id;
        setBatchId(data.batch_id);
        if (data.final) void loadComparison(data.batch_id);
      },
      extraction_plan_ready: () => void refetchPlan(),
      enrichment_done: () => { void refetchMetadata(); void refetchDoc(); },
      indexed: () => void refetchDoc(),
    });
  }, [id, loadComparison, refetchDoc, refetchMetadata, refetchPlan]);

  const status = snapshot?.status ?? doc?.status ?? "uploaded";
  const pendingEngines = Object.values(progress)
    .filter((p) => p.status === "queued" || p.status === "running")
    .map((p) => p.engine);

  async function runOcr() {
    if (!selected.length) return;
    setBusy(true);
    setComparison(null);
    setStartedAt(Date.now());
    setProgress(
      Object.fromEntries(
        selected.map((e) => [e, { engine: e, status: "queued" as const, page: 0,
                                  of: doc?.page_count ?? 1 }]),
      ),
    );
    try {
      const batch = await startOcr(id, selected, ["ara", "eng"]);
      batchRef.current = batch.id;
      setBatchId(batch.id);
    } finally {
      setBusy(false);
    }
  }

  async function useRun(runId: string) {
    const revision = await selectRun(id, runId);
    setText(revision.text);
    void refetchDoc();
  }

  async function openChat() {
    const conversation = await createConversation("selected", [id]);
    router.push(`/chat/${conversation.id}`);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="doc-text text-lg font-semibold" dir="auto">
          {doc?.display_title ?? "…"}
        </h1>
        <StatusBadge status={status} />
        {doc?.is_digital_pdf && (
          <span className="rounded bg-green-500/10 px-2 py-0.5 text-xs text-green-700">
            has text layer
          </span>
        )}
        {/* The "chat with this document later" entry point. */}
        {["ready", "indexed"].includes(status) && (
          <button
            onClick={openChat}
            className="ms-auto rounded-md px-3 py-1.5 text-sm text-white"
            style={{ background: "var(--accent)" }}
          >
            {t("chatWithDoc")}
          </button>
        )}
      </div>

      {doc?.digital_text_report?.reason && (
        <p className="text-xs" style={{ color: "var(--muted)" }}>
          {doc.digital_text_report.reason}
        </p>
      )}

      <EngineSelector
        engines={engines}
        selected={selected}
        onChange={setSelected}
        pageCount={doc?.page_count ?? 1}
        isDigital={!!doc?.is_digital_pdf}
        onRun={runOcr}
        busy={busy || pendingEngines.length > 0}
      />

      <OcrProgressPanel progress={progress} engines={engines} startedAt={startedAt} />

      <OcrComparisonGrid
        comparison={comparison}
        pendingEngines={pendingEngines}
        onSelect={useRun}
      />

      <TextEditor
        documentId={id}
        initialText={text}
        currentRevisionNo={doc?.current_revision_no ?? null}
        onSave={async (value) => { await saveRevision(id, value); void refetchDoc(); }}
        onFinalize={async () => {
          await finalizeText(id);
          await enrich(id, doc?.metadata_mode ?? "auto");
          void refetchDoc();
        }}
      />

      <MetadataPanel metadata={metadata} plan={plan} />
    </div>
  );
}
