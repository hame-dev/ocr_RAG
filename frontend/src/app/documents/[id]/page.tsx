"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ChevronRight, FileQuestion, Layers, Loader2, MessagesSquare, MoreHorizontal, RotateCw, ScanText,
} from "lucide-react";
import {
  ApiError, createConversation, getDocument, getExtractionPlan, getMetadata, listEngines,
  retryDocument, selectRun, startOcr,
} from "@/lib/api";
import { pages as pagesLabel, relativeTime } from "@/lib/format";
import { useLocale } from "@/components/Providers";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, Page } from "@/components/app-shell/Page";
import { ChatStep } from "@/components/document/ChatStep";
import { DetailsStep } from "@/components/document/DetailsStep";
import { ExtractStep } from "@/components/document/ExtractStep";
import { ReviewStep } from "@/components/document/ReviewStep";
import { CHAT_READY, initialStep, Step, Stepper, stepProgress } from "@/components/document/Stepper";
import { useDocumentStream } from "@/components/document/useDocumentStream";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";

const PREPARING = ["uploaded", "preprocessing"];

export default function DocumentPage() {
  const { t, locale } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();
  const [step, setStep] = useState<Step | null>(null);

  const docQuery = useQuery({
    queryKey: ["document", id],
    queryFn: () => getDocument(id),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  const doc = docQuery.data;
  const { data: engineData } = useQuery({ queryKey: ["engines"], queryFn: () => listEngines() });
  const metadataQuery = useQuery({
    queryKey: ["metadata", id],
    queryFn: () => getMetadata(id).catch(() => null),
  });
  const planQuery = useQuery({
    queryKey: ["plan", id],
    queryFn: () => getExtractionPlan(id).catch(() => null),
  });

  const stream = useDocumentStream(id, {
    onStatus: (to) => {
      void queryClient.invalidateQueries({ queryKey: ["document", id] });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      if (["enriched", "ready", "indexed"].includes(to)) {
        void metadataQuery.refetch();
        void planQuery.refetch();
      }
    },
    onMetadata: () => {
      void metadataQuery.refetch();
      void planQuery.refetch();
      void queryClient.invalidateQueries({ queryKey: ["document", id] });
    },
  });

  const status = stream.liveStatus ?? doc?.status ?? "uploaded";
  const revisionNo = doc?.current_revision_no ?? null;
  const done = stepProgress(status, revisionNo);

  // Open on the step that matches where the document is, once.
  useEffect(() => {
    if (doc && step === null) setStep(initialStep(doc.status, doc.current_revision_no));
  }, [doc, step]);

  if (docQuery.isError) {
    const missing = docQuery.error instanceof ApiError && docQuery.error.status === 404;
    return (
      <Page>
        <EmptyState
          icon={<FileQuestion />}
          title={missing ? t("documentNotFound") : t("loadFailed")}
          description={missing ? t("documentNotFoundHint") : undefined}
          action={
            missing
              ? <Button asChild variant="outline"><Link href="/">{t("backToLibrary")}</Link></Button>
              : <Button variant="outline" onClick={() => docQuery.refetch()}>{t("retry")}</Button>
          }
        />
      </Page>
    );
  }

  if (!doc || step === null) {
    return (
      <Page>
        <Skeleton className="mb-3 h-4 w-40" />
        <Skeleton className="mb-6 h-8 w-72" />
        <Skeleton className="mb-6 h-9 w-full max-w-lg" />
        <Skeleton className="h-96 w-full rounded-xl" />
      </Page>
    );
  }

  async function runOcr(engines: string[]) {
    try {
      const batch = await startOcr(id, engines, ["ara", "eng"]);
      stream.beginBatch(batch.id, engines, doc?.page_count ?? 1);
      toast.success(t("ocrStarted"));
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    }
  }

  async function pickResult(runId: string) {
    try {
      const revision = await selectRun(id, runId);
      queryClient.setQueryData(["revision", id, revision.revision_no], revision);
      await queryClient.invalidateQueries({ queryKey: ["document", id] });
      setStep("review");
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    }
  }

  async function openChat() {
    try {
      const conversation = await createConversation("selected", [id]);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      router.push(`/chat/${conversation.id}`);
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    }
  }

  async function retry() {
    const stage = revisionNo == null ? "preprocess" : done.review ? "enrich" : "preprocess";
    try {
      await retryDocument(id, stage);
      toast.success(t("retryProcessing"));
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    }
  }

  const failed = status === "failed" || status === "ocr_failed";

  return (
    <Page className="max-w-7xl">
      {/* Header */}
      <nav className="mb-2 flex items-center gap-1 text-sm text-muted-foreground" aria-label="Breadcrumb">
        <Link href="/" className="hover:text-foreground">{t("library")}</Link>
        <ChevronRight className="size-3.5 rtl:-scale-x-100" />
        <span className="truncate text-foreground" dir="auto">{doc.display_title}</span>
      </nav>

      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-2xl font-semibold tracking-tight" dir="auto">{doc.display_title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={status} />
            {doc.page_count != null && (
              <span className="flex items-center gap-1"><Layers className="size-3.5" /> {pagesLabel(doc.page_count, t)}</span>
            )}
            {doc.is_digital_pdf && <Badge variant="outline">{t("hasTextLayer")}</Badge>}
            <span>{relativeTime(doc.created_at, locale)}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {CHAT_READY.includes(status) && (
            <Button onClick={() => void openChat()}>
              <MessagesSquare /> {t("chatWithDoc")}
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" aria-label={t("moreActions")}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setStep("extract")}>
                <ScanText /> {t("runOcr")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void retry()}>
                <RotateCw /> {t("retryProcessing")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {failed && doc.error_message && (
        <Alert variant="destructive" className="mb-6">
          <AlertDescription className="flex flex-wrap items-center gap-3">
            <span className="min-w-0 flex-1">{doc.error_message}</span>
            <Button size="sm" variant="outline" onClick={() => void retry()}><RotateCw /> {t("retryProcessing")}</Button>
          </AlertDescription>
        </Alert>
      )}

      {PREPARING.includes(status) && (
        <Alert className="mb-6">
          <Loader2 className="animate-spin" />
          <AlertDescription>{t("processing")}</AlertDescription>
        </Alert>
      )}

      <div className="mb-6 border-b pb-4">
        <Stepper current={step} done={done} onSelect={setStep} />
      </div>

      <div key={step} className="animate-fade-in">
        {step === "extract" && (
          <ExtractStep
            engines={engineData?.engines ?? []}
            defaultEngine={engineData?.default_engine ?? null}
            pageCount={doc.page_count ?? 1}
            isDigital={!!doc.is_digital_pdf}
            preparing={PREPARING.includes(status)}
            progress={stream.progress}
            pending={stream.pending}
            comparison={stream.comparison}
            onRun={runOcr}
            onUseResult={pickResult}
          />
        )}
        {step === "review" && (
          <ReviewStep doc={doc} onGoToExtract={() => setStep("extract")} onFinalized={() => setStep("details")} />
        )}
        {step === "details" && (
          <DetailsStep status={status} metadata={metadataQuery.data} plan={planQuery.data} onRetry={retry} />
        )}
        {step === "chat" && (
          <ChatStep documentId={id} ready={CHAT_READY.includes(status)} onStart={openChat} />
        )}
      </div>
    </Page>
  );
}
