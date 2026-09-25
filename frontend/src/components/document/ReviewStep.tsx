"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, FileText, ImageIcon, ImageOff, Loader2,
  Save, ScanText, Sparkles, ZoomIn, ZoomOut,
} from "lucide-react";
import {
  api, ApiError, DocumentDetail, enrich, finalizeText, getRevision, pageImageUrl, saveRevision,
} from "@/lib/api";
import { fmt, num } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Hint } from "@/components/ui/tooltip";
import { EmptyState } from "../app-shell/Page";
import { useLocale } from "../Providers";

/** Pages are separated by \f everywhere: chunker, editor, diff, citations. */
const PAGE_BREAK = "\f";

interface CorrectionChange {
  id: string;
  page: number;
  original: string;
  replacement: string;
  kind: string;
  confidence?: number;
  reason?: string;
  auto_accept?: boolean;
}

interface CorrectionJob {
  job_id: string;
  status: string;
  changes: CorrectionChange[];
  rejected: { rule: string; original: string; replacement: string }[];
}

export function ReviewStep({
  doc, onGoToExtract, onFinalized,
}: {
  doc: DocumentDetail;
  onGoToExtract: () => void;
  onFinalized: () => void;
}) {
  const { t } = useLocale();
  const queryClient = useQueryClient();
  const revisionNo = doc.current_revision_no;

  const revision = useQuery({
    queryKey: ["revision", doc.id, revisionNo],
    queryFn: () => getRevision(doc.id, revisionNo!),
    enabled: revisionNo != null,
    staleTime: Infinity,
  });

  const [pages, setPages] = useState<string[]>([]);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [mobilePane, setMobilePane] = useState<"image" | "text">("text");
  const [busy, setBusy] = useState<null | "save" | "final" | "ai">(null);
  const [correction, setCorrection] = useState<CorrectionJob | null>(null);

  // Seed the editor from the saved revision. This is what used to be missing:
  // reopening a document showed an empty editor, and Save would wipe the text.
  useEffect(() => {
    if (revision.data && revision.data.text !== baseline) {
      const text = revision.data.text;
      if (baseline === null || pages.join(PAGE_BREAK) === baseline) {
        setPages(text.split(PAGE_BREAK));
        setBaseline(text);
      }
    }
    // Only react to a newly loaded revision, never to local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision.data]);

  const text = pages.join(PAGE_BREAK);
  const dirty = baseline !== null && text !== baseline;
  const pageCount = Math.max(doc.pages?.length ?? 0, pages.length, 1);
  const pageInfo = doc.pages?.find((p) => p.page_number === pageIndex + 1);

  // Warn before losing unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (revisionNo == null) {
    return (
      <EmptyState
        icon={<ScanText />}
        title={t("noTextYet")}
        description={t("noTextYetHint")}
        action={<Button onClick={onGoToExtract}>{t("goToExtract")}</Button>}
      />
    );
  }

  const refreshDocument = () => queryClient.invalidateQueries({ queryKey: ["document", doc.id] });

  async function save(): Promise<boolean> {
    // Never overwrite a revision with nothing, or re-save what is already saved.
    if (!dirty || !text.trim()) return true;
    setBusy("save");
    try {
      const saved = await saveRevision(doc.id, text);
      setBaseline(saved.text);
      queryClient.setQueryData(["revision", doc.id, saved.revision_no], saved);
      await refreshDocument();
      toast.success(t("saved"));
      return true;
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof Error ? error.message : undefined });
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function finalize() {
    if (!(await save())) return;
    setBusy("final");
    try {
      await finalizeText(doc.id);
      await enrich(doc.id, doc.metadata_mode ?? "auto");
      await refreshDocument();
      toast.success(t("finalizedToast"));
      onFinalized();
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    } finally {
      setBusy(null);
    }
  }

  async function improve() {
    if (!(await save())) return;
    setBusy("ai");
    try {
      const job = await api<{ job_id: string }>(`/api/documents/${doc.id}/ai-correct/`, {
        method: "POST",
        body: JSON.stringify({ multimodal: true }),
      });
      // Corrections are proposals; nothing is applied until the user accepts.
      for (let i = 0; i < 150; i++) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const state = await api<CorrectionJob>(`/api/ai-corrections/${job.job_id}/`);
        if (state.status === "ready") {
          if (!state.changes?.length && !state.rejected?.length) toast(t("aiNoChanges"));
          else setCorrection(state);
          return;
        }
        if (state.status === "failed") throw new Error(t("aiFailed"));
      }
      throw new Error(t("aiFailed"));
    } catch (error) {
      toast.error(t("aiFailed"), { description: error instanceof Error ? error.message : undefined });
    } finally {
      setBusy(null);
    }
  }

  async function applyCorrections(ids: string[]) {
    if (!correction) return;
    try {
      const result = await api<{ revision: { text: string; revision_no: number } }>(
        `/api/ai-corrections/${correction.job_id}/apply/`,
        { method: "POST", body: JSON.stringify({ accepted_ids: ids }) },
      );
      setPages(result.revision.text.split(PAGE_BREAK));
      setBaseline(result.revision.text);
      setCorrection(null);
      await refreshDocument();
      toast.success(t("saved"));
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof Error ? error.message : undefined });
    }
  }

  const pager = (
    <div className="flex items-center gap-1">
      <Button variant="outline" size="icon-sm" disabled={pageIndex === 0}
              onClick={() => setPageIndex((i) => i - 1)} aria-label={t("previousPage")}>
        <ChevronLeft className="rtl:-scale-x-100" />
      </Button>
      <span className="tabular-nums min-w-20 text-center text-xs text-muted-foreground">
        {fmt(t("pageOf"), { page: num(pageIndex + 1), of: num(pageCount) })}
      </span>
      <Button variant="outline" size="icon-sm" disabled={pageIndex >= pageCount - 1}
              onClick={() => setPageIndex((i) => i + 1)} aria-label={t("nextPage")}>
        <ChevronRight className="rtl:-scale-x-100" />
      </Button>
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Toolbar: sticky so actions stay reachable while scrolling a long page. */}
      <div className="sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-2 bg-background/85 px-1 py-2 backdrop-blur">
        {pager}
        <Badge variant="outline" className="tabular-nums">{fmt(t("revisionN"), { n: num(revisionNo) })}</Badge>
        {dirty ? (
          <span className="flex items-center gap-1.5 text-xs text-warning">
            <span className="size-1.5 rounded-full bg-current" />{t("unsavedChanges")}
          </span>
        ) : baseline !== null ? (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CheckCircle2 className="size-3.5" />{t("saved")}
          </span>
        ) : null}

        <div className="ms-auto flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => void improve()} disabled={!!busy || !baseline}>
            {busy === "ai" ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {busy === "ai" ? t("aiRunning") : t("aiImprove")}
          </Button>
          <Button variant="outline" onClick={() => void save()} disabled={!!busy || !dirty || !text.trim()}>
            {busy === "save" ? <Loader2 className="animate-spin" /> : <Save />}
            {t("save")}
          </Button>
          <Hint label={t("finalizeHint")}>
            <Button onClick={() => void finalize()} disabled={!!busy || !baseline || !text.trim()}>
              {busy === "final" ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
              {t("finalize")}
            </Button>
          </Hint>
        </div>
      </div>

      <Segmented
        className="lg:hidden"
        value={mobilePane}
        onChange={setMobilePane}
        options={[
          { value: "text", label: t("text"), icon: <FileText /> },
          { value: "image", label: t("pageImage"), icon: <ImageIcon /> },
        ]}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className={cn(mobilePane !== "image" && "hidden lg:block")}>
          <PageViewer documentId={doc.id} page={pageIndex + 1} available={pageInfo?.has_raster ?? true} />
        </div>
        <div className={cn(mobilePane !== "text" && "hidden lg:block")}>
          <Card className="overflow-hidden">
            {revision.isPending ? (
              <div className="space-y-3 p-5">
                {[90, 75, 85, 60, 80, 70].map((width, i) => (
                  <Skeleton key={i} className="h-4" style={{ width: `${width}%` }} />
                ))}
              </div>
            ) : (
              // dir="auto" + .doc-text (unicode-bidi: plaintext): without them a
              // bilingual paragraph renders scrambled and the user blames the OCR.
              <textarea
                dir="auto"
                aria-label={fmt(t("pageOf"), { page: num(pageIndex + 1), of: num(pageCount) })}
                value={pages[pageIndex] ?? ""}
                onChange={(event) =>
                  setPages((current) => {
                    const next = [...current];
                    while (next.length <= pageIndex) next.push("");
                    next[pageIndex] = event.target.value;
                    return next;
                  })
                }
                className="doc-text scrollbar-thin block h-[65vh] min-h-80 w-full resize-none bg-card p-5 text-[15px] focus:outline-none"
              />
            )}
          </Card>
        </div>
      </div>

      {correction && (
        <CorrectionDialog
          job={correction}
          onClose={() => setCorrection(null)}
          onApply={applyCorrections}
        />
      )}
    </div>
  );
}

function PageViewer({ documentId, page, available }: { documentId: string; page: number; available: boolean }) {
  const { t } = useLocale();
  const [zoomed, setZoomed] = useState(false);
  const [failed, setFailed] = useState(false);
  const src = useMemo(() => pageImageUrl(documentId, page), [documentId, page]);

  useEffect(() => setFailed(false), [src]);

  return (
    <Card className="relative overflow-hidden bg-muted/40">
      {available && !failed ? (
        <>
          <div className={cn("scrollbar-thin h-[65vh] min-h-80", zoomed ? "overflow-auto" : "grid place-items-center overflow-hidden p-4")}>
            {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
            <img
              key={src}
              src={src}
              alt={`${t("pageImage")} ${num(page)}`}
              onError={() => setFailed(true)}
              className={cn(
                "rounded-md bg-white shadow-sm animate-fade-in",
                zoomed ? "max-w-none" : "max-h-full max-w-full object-contain",
              )}
              style={zoomed ? { width: "160%" } : undefined}
            />
          </div>
          <Button
            variant="secondary"
            size="icon-sm"
            className="absolute end-3 top-3 shadow-sm"
            onClick={() => setZoomed((value) => !value)}
            aria-label={zoomed ? t("zoomOut") : t("zoomIn")}
          >
            {zoomed ? <ZoomOut /> : <ZoomIn />}
          </Button>
        </>
      ) : (
        <div className="grid h-[65vh] min-h-80 place-items-center text-muted-foreground">
          <ImageOff className="size-8" />
        </div>
      )}
    </Card>
  );
}

function CorrectionDialog({
  job, onClose, onApply,
}: {
  job: CorrectionJob;
  onClose: () => void;
  onApply: (ids: string[]) => Promise<void>;
}) {
  const { t } = useLocale();
  const changes = job.changes ?? [];
  const rejected = job.rejected ?? [];
  const [accepted, setAccepted] = useState<string[]>(
    changes.filter((c) => c.auto_accept).map((c) => c.id),
  );
  const [showRejected, setShowRejected] = useState(false);
  const [applying, setApplying] = useState(false);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl" closeLabel={t("close")}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-primary" /> {t("aiReviewTitle")}
          </DialogTitle>
          <DialogDescription>
            {fmt(t("correctionsCount"), { n: num(changes.length) })} · {t("aiReviewHint")}
          </DialogDescription>
        </DialogHeader>

        <ul className="scrollbar-thin -mx-2 max-h-[50vh] space-y-1 overflow-y-auto px-2">
          {changes.map((change) => {
            const checked = accepted.includes(change.id);
            return (
              <li key={change.id}>
                <label className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                  checked ? "border-primary/40 bg-primary/5" : "hover:bg-accent/50",
                )}>
                  <Checkbox
                    className="mt-1"
                    checked={checked}
                    onCheckedChange={(value) =>
                      setAccepted((prev) => value ? [...prev, change.id] : prev.filter((id) => id !== change.id))
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <div className="doc-text text-sm" dir="auto">
                      <span className="rounded bg-destructive/10 px-1 text-destructive line-through decoration-destructive/40">
                        {change.original}
                      </span>
                      <span className="mx-1.5 text-muted-foreground">→</span>
                      <span className="rounded bg-success/10 px-1 text-success">{change.replacement}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      <span className="tabular-nums">{t("pageShort")}{num(change.page)}</span>
                      {change.confidence != null && <> · <span className="tabular-nums">{num(change.confidence * 100)}%</span></>}
                      {change.reason && <> · {change.reason}</>}
                    </p>
                  </div>
                </label>
              </li>
            );
          })}
        </ul>

        {rejected.length > 0 && (
          <div className="rounded-lg border">
            <button
              type="button"
              onClick={() => setShowRejected((value) => !value)}
              className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
              aria-expanded={showRejected}
            >
              <ChevronDown className={cn("size-3.5 transition-transform", showRejected && "rotate-180")} />
              {fmt(t("rejectedByGuards"), { n: num(rejected.length) })}
            </button>
            {showRejected && (
              <ul className="space-y-1 border-t px-3 py-2 text-xs text-muted-foreground">
                {rejected.map((item, index) => (
                  <li key={index} className="doc-text" dir="auto">
                    <code className="me-1.5 rounded bg-muted px-1 font-sans" dir="ltr">{item.rule}</code>
                    {item.original} → {item.replacement}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("cancel")}</Button>
          <Button
            disabled={!accepted.length || applying}
            onClick={async () => {
              setApplying(true);
              try { await onApply(accepted); } finally { setApplying(false); }
            }}
          >
            {applying && <Loader2 className="animate-spin" />}
            {fmt(t("applyN"), { n: num(accepted.length) })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
