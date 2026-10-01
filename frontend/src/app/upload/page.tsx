"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, CheckCircle2, CloudUpload, FileImage, FileSearch, FileText, Loader2,
  MessagesSquare, Plus, ScanText, Sparkles, X,
} from "lucide-react";
import { uploadDocument } from "@/lib/api";
import { bytes } from "@/lib/format";
import { fmt, StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useLocale } from "@/components/Providers";
import { Page, PageHeader } from "@/components/app-shell/Page";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MAX_MB = 50;
// Each file is its own POST to /api/documents/, so the per-file limits and
// validation stay exactly what the backend enforces; this only caps the batch.
const MAX_FILES = 5;
const ACCEPT = ".pdf,image/*";

const STEPS: { icon: typeof ScanText; title: StringKey; hint: StringKey }[] = [
  { icon: ScanText, title: "stepExtract", hint: "stepExtractHint" },
  { icon: FileSearch, title: "stepReview", hint: "stepReviewHint" },
  { icon: Sparkles, title: "stepDetails", hint: "stepDetailsHint" },
  { icon: MessagesSquare, title: "stepChat", hint: "stepChatHint" },
];

type Status = "waiting" | "uploading" | "done" | "failed";

type Item = {
  key: string;
  file: File;
  status: Status;
  error?: string;
  documentId?: string;
};

function isSupported(file: File) {
  return file.type === "application/pdf" || file.type.startsWith("image/")
    || /\.(pdf|png|jpe?g|tiff?|webp|bmp)$/i.test(file.name);
}

let nextKey = 0;

export default function UploadPage() {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const pending = items.filter((item) => item.status !== "done");
  const failed = items.filter((item) => item.status === "failed");
  const single = items.length === 1;

  function choose(list: FileList | File[] | null | undefined) {
    setError(null);
    const picked = Array.from(list ?? []);
    if (!picked.length) return;

    const accepted: File[] = [];
    for (const file of picked) {
      if (!isSupported(file)) {
        setError(`${file.name}: ${t("unsupportedFile")}`);
        continue;
      }
      if (file.size > MAX_MB * 1024 * 1024) {
        setError(`${file.name}: ${fmt(t("fileTooLarge"), { size: MAX_MB })}`);
        continue;
      }
      accepted.push(file);
    }

    const room = MAX_FILES - items.length;
    if (accepted.length > room) setError(fmt(t("tooManyFiles"), { max: MAX_FILES }));
    const added = accepted.slice(0, Math.max(0, room)).map((file) => ({
      key: `${file.name}-${nextKey++}`, file, status: "waiting" as Status,
    }));
    if (added.length) setItems((current) => [...current, ...added]);
  }

  function remove(key: string) {
    setItems((current) => current.filter((item) => item.key !== key));
    if (items.length <= 1) setTitle("");
  }

  function update(key: string, patch: Partial<Item>) {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)));
  }

  async function send() {
    const queue = items.filter((item) => item.status === "waiting" || item.status === "failed");
    if (!queue.length) return;
    setBusy(true);
    setError(null);

    // One request per file, in order: a failure leaves that file marked and
    // the rest still go through.
    let uploaded = 0;
    let lastDocumentId: string | undefined;
    for (const item of queue) {
      update(item.key, { status: "uploading", error: undefined });
      try {
        const doc = await uploadDocument(item.file, single ? title.trim() || undefined : undefined);
        update(item.key, { status: "done", documentId: doc.id });
        uploaded += 1;
        lastDocumentId = doc.id;
      } catch (e) {
        update(item.key, { status: "failed", error: e instanceof Error ? e.message : String(e) });
      }
    }

    if (uploaded) void queryClient.invalidateQueries({ queryKey: ["documents"] });
    const failures = queue.length - uploaded;
    if (failures) {
      setError(fmt(t("someUploadsFailed"), { n: failures }));
      setBusy(false);
      return;
    }
    // A single file keeps the original flow: straight to its review page.
    router.push(single && lastDocumentId ? `/documents/${lastDocumentId}` : "/");
  }

  const doneCount = items.filter((item) => item.status === "done").length;
  const buttonLabel = busy
    ? fmt(t("uploadProgress"), { done: doneCount + 1, total: items.length })
    : single
      ? t("uploadAndContinue")
      : fmt(t("uploadAll"), { n: pending.length });

  return (
    <Page className="max-w-3xl">
      <PageHeader title={t("uploadTitle")} description={fmt(t("uploadSubtitle"), { max: MAX_FILES, size: MAX_MB })} />

      <Card className="p-2">
        {items.length === 0 ? (
          <div
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                inputRef.current?.click();
              }
            }}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              choose(event.dataTransfer.files);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-16 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              dragging ? "border-primary bg-primary/5" : "border-border hover:border-foreground/25 hover:bg-muted/40",
            )}
          >
            <span className="mb-4 grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary">
              <CloudUpload className="size-7" />
            </span>
            <p className="font-medium">{t("dropHere")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("supportedFiles")}</p>
            <Button variant="outline" size="sm" className="pointer-events-none mt-5" tabIndex={-1}>
              {t("browseFiles")}
            </Button>
          </div>
        ) : (
          <div
            className="space-y-5 p-4"
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (!busy) choose(event.dataTransfer.files);
            }}
          >
            <ul className="space-y-2" aria-label={t("uploadTitle")}>
              {items.map((item) => {
                const FileIcon = item.file.type.startsWith("image/") ? FileImage : FileText;
                return (
                  <li
                    key={item.key}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border p-3",
                      item.status === "failed" ? "border-destructive/40 bg-destructive/5" : "bg-muted/40",
                    )}
                  >
                    <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-background text-muted-foreground">
                      <FileIcon className="size-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium" dir="auto">{item.file.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {bytes(item.file.size)}
                        {items.length > 1 && (
                          <>
                            {" · "}
                            <span className={cn(item.status === "failed" && "text-destructive")}>
                              {item.status === "waiting" && t("fileWaiting")}
                              {item.status === "uploading" && t("uploading")}
                              {item.status === "done" && t("fileUploaded")}
                              {item.status === "failed" && `${t("fileFailed")}: ${item.error}`}
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                    {item.status === "uploading" && <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />}
                    {item.status === "done" && <CheckCircle2 className="size-4 shrink-0 text-primary" />}
                    {(item.status === "waiting" || item.status === "failed") && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => remove(item.key)}
                        disabled={busy}
                        aria-label={t("removeFile")}
                      >
                        <X />
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>

            {items.length < MAX_FILES && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => inputRef.current?.click()}
                disabled={busy}
                className={cn("w-full border-dashed", dragging && "border-primary bg-primary/5")}
              >
                <Plus />
                {t("addMoreFiles")}
              </Button>
            )}

            {single && (
              <div className="space-y-2">
                <Label htmlFor="title">{t("titleOptional")}</Label>
                <Input
                  id="title"
                  dir="auto"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder={t("titlePlaceholder")}
                  disabled={busy}
                />
              </div>
            )}

            <div className="flex justify-end gap-2">
              {doneCount > 0 && !busy && (
                <Button variant="outline" onClick={() => router.push("/")}>
                  {t("openLibrary")}
                </Button>
              )}
              <Button onClick={() => void send()} disabled={busy || pending.length === 0}>
                {busy && <Loader2 className="animate-spin" />}
                {buttonLabel}
              </Button>
            </div>
          </div>
        )}
      </Card>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(event) => {
          choose(event.target.files);
          event.target.value = "";
        }}
      />

      {error && (
        <Alert variant="destructive" className="mt-4">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {failed.length > 0 && !busy && (
        <p className="sr-only" role="status">{fmt(t("someUploadsFailed"), { n: failed.length })}</p>
      )}

      <section className="mt-10">
        <h2 className="mb-4 text-sm font-medium text-muted-foreground">{t("howItWorks")}</h2>
        <ol className="grid gap-3 sm:grid-cols-2">
          {STEPS.map(({ icon: Icon, title: stepTitle, hint }, index) => (
            <li key={stepTitle} className="flex gap-3 rounded-xl border p-4">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                <Icon className="size-4" />
              </span>
              <div>
                <p className="text-sm font-medium">
                  <span className="tabular-nums me-1.5 text-muted-foreground">{index + 1}.</span>
                  {t(stepTitle)}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">{t(hint)}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </Page>
  );
}
