"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, CloudUpload, FileImage, FileSearch, FileText, Loader2, MessagesSquare,
  ScanText, Sparkles, X,
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
const ACCEPT = ".pdf,image/*";

const STEPS: { icon: typeof ScanText; title: StringKey; hint: StringKey }[] = [
  { icon: ScanText, title: "stepExtract", hint: "stepExtractHint" },
  { icon: FileSearch, title: "stepReview", hint: "stepReviewHint" },
  { icon: Sparkles, title: "stepDetails", hint: "stepDetailsHint" },
  { icon: MessagesSquare, title: "stepChat", hint: "stepChatHint" },
];

function isSupported(file: File) {
  return file.type === "application/pdf" || file.type.startsWith("image/")
    || /\.(pdf|png|jpe?g|tiff?|webp|bmp)$/i.test(file.name);
}

export default function UploadPage() {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  function choose(next: File | undefined) {
    setError(null);
    if (!next) return;
    if (!isSupported(next)) return setError(t("unsupportedFile"));
    if (next.size > MAX_MB * 1024 * 1024) return setError(fmt(t("fileTooLarge"), { size: MAX_MB }));
    setFile(next);
  }

  async function send() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const doc = await uploadDocument(file, title.trim() || undefined);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setError(`${t("uploadFailed")}: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
    }
  }

  const FileIcon = file?.type.startsWith("image/") ? FileImage : FileText;

  return (
    <Page className="max-w-3xl">
      <PageHeader title={t("uploadTitle")} description={fmt(t("uploadSubtitle"), { size: MAX_MB })} />

      <Card className="p-2">
        {!file ? (
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
              choose(event.dataTransfer.files?.[0]);
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
          <div className="space-y-5 p-4">
            <div className="flex items-center gap-3 rounded-lg border bg-muted/40 p-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-background text-muted-foreground">
                <FileIcon className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" dir="auto">{file.name}</p>
                <p className="text-xs text-muted-foreground">{bytes(file.size)}</p>
              </div>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => { setFile(null); setTitle(""); }}
                disabled={busy}
                aria-label={t("removeFile")}
              >
                <X />
              </Button>
            </div>

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

            <div className="flex justify-end">
              <Button onClick={() => void send()} disabled={busy}>
                {busy && <Loader2 className="animate-spin" />}
                {busy ? t("uploading") : t("uploadAndContinue")}
              </Button>
            </div>
          </div>
        )}
      </Card>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(event) => {
          choose(event.target.files?.[0]);
          event.target.value = "";
        }}
      />

      {error && (
        <Alert variant="destructive" className="mt-4">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
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
