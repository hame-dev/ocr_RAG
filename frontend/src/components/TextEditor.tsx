"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { num } from "@/lib/i18n";
import { useLocale } from "./Providers";

/**
 * Manual editing, plus the entry point for AI correction.
 *
 * Page separators are \f throughout the system; they render as visual dividers
 * here so the user can see page boundaries while editing.
 */
export function TextEditor({
  documentId, initialText, currentRevisionNo, onSave, onFinalize,
}: {
  documentId: string;
  initialText: string;
  currentRevisionNo: number | null;
  onSave: (text: string) => Promise<void>;
  onFinalize: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [text, setText] = useState(initialText);
  const [busy, setBusy] = useState<string | null>(null);
  const [correction, setCorrection] = useState<any>(null);

  useEffect(() => { if (initialText) setText(initialText); }, [initialText]);

  if (!text && currentRevisionNo == null) return null;

  async function improve() {
    setBusy("ai");
    try {
      const job = await api<any>(`/api/documents/${documentId}/ai-correct/`, {
        method: "POST",
        body: JSON.stringify({ multimodal: true }),
      });
      // Poll until the proposals are ready. Corrections are never auto-applied.
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const state = await api<any>(`/api/ai-corrections/${job.job_id}/`);
        if (state.status === "ready") { setCorrection(state); break; }
        if (state.status === "failed") break;
      }
    } finally {
      setBusy(null);
    }
  }

  async function applyCorrections(ids: string[]) {
    const result = await api<any>(`/api/ai-corrections/${correction.job_id}/apply/`, {
      method: "POST",
      body: JSON.stringify({ accepted_ids: ids }),
    });
    setText(result.revision.text);
    setCorrection(null);
  }

  return (
    <section className="card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="font-medium">{t("editText")}</h2>
        {currentRevisionNo != null && (
          <span className="numeric text-xs" style={{ color: "var(--muted)" }}>
            rev {num(currentRevisionNo)}
          </span>
        )}
        <div className="ms-auto flex gap-2">
          <button
            onClick={improve}
            disabled={!!busy}
            className="rounded-md border px-3 py-1.5 text-sm disabled:opacity-40"
            style={{ borderColor: "var(--border)" }}
          >
            {busy === "ai" ? "…" : t("aiImprove")}
          </button>
          <button
            onClick={async () => { setBusy("save"); await onSave(text); setBusy(null); }}
            disabled={!!busy}
            className="rounded-md border px-3 py-1.5 text-sm disabled:opacity-40"
            style={{ borderColor: "var(--border)" }}
          >
            {t("save")}
          </button>
          <button
            onClick={async () => { setBusy("final"); await onFinalize(); setBusy(null); }}
            disabled={!!busy}
            className="rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-40"
            style={{ background: "var(--accent)" }}
          >
            {busy === "final" ? "…" : t("finalize")}
          </button>
        </div>
      </div>

      {/* dir="auto" is essential: without it a bilingual paragraph renders
          scrambled and the user blames the OCR. */}
      <textarea
        dir="auto"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={18}
        className="doc-text w-full rounded-lg border p-3 text-sm"
        style={{ borderColor: "var(--border)", background: "var(--bg)", color: "var(--fg)" }}
      />

      {correction && <CorrectionReview job={correction} onApply={applyCorrections} />}
    </section>
  );
}

/**
 * Per-change accept/reject. Nothing is applied without the user.
 *
 * Guard-rejected proposals are listed too, with the rule that killed them, so
 * the safety layer is auditable rather than a black box.
 */
function CorrectionReview({
  job, onApply,
}: {
  job: any;
  onApply: (ids: string[]) => void;
}) {
  const [accepted, setAccepted] = useState<string[]>(
    (job.changes ?? []).filter((c: any) => c.auto_accept).map((c: any) => c.id),
  );
  const [showRejected, setShowRejected] = useState(false);

  const changes = job.changes ?? [];
  const rejected = job.rejected ?? [];

  return (
    <div className="mt-4 rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-medium">
          {changes.length} proposed correction{changes.length === 1 ? "" : "s"}
        </h3>
        <button
          onClick={() => onApply(accepted)}
          disabled={!accepted.length}
          className="ms-auto rounded-md px-3 py-1 text-sm text-white disabled:opacity-40"
          style={{ background: "var(--accent)" }}
        >
          Apply {accepted.length}
        </button>
      </div>

      <ul className="max-h-72 space-y-1.5 overflow-y-auto">
        {changes.map((c: any) => (
          <li key={c.id} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={accepted.includes(c.id)}
              onChange={(e) =>
                setAccepted((prev) =>
                  e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id),
                )
              }
              className="mt-1"
            />
            <div className="min-w-0 flex-1">
              <div className="doc-text" dir="auto">
                <span style={{ background: "rgba(239,68,68,0.15)" }}>{c.original}</span>
                {" → "}
                <span style={{ background: "rgba(34,197,94,0.18)" }}>{c.replacement}</span>
              </div>
              <div className="text-xs" style={{ color: "var(--muted)" }}>
                {c.kind} · p{c.page} · {Math.round((c.confidence ?? 0) * 100)}% · {c.reason}
              </div>
            </div>
          </li>
        ))}
      </ul>

      {rejected.length > 0 && (
        <div className="mt-3 border-t pt-2" style={{ borderColor: "var(--border)" }}>
          <button
            onClick={() => setShowRejected((v) => !v)}
            className="text-xs underline"
            style={{ color: "var(--muted)" }}
          >
            {rejected.length} rejected by safety guards {showRejected ? "▾" : "▸"}
          </button>
          {showRejected && (
            <ul className="mt-2 space-y-1 text-xs" style={{ color: "var(--muted)" }}>
              {rejected.map((r: any, i: number) => (
                <li key={i} className="doc-text" dir="auto">
                  <code className="rounded bg-black/5 px-1">{r.rule}</code>{" "}
                  {r.original} → {r.replacement}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
