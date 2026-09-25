"use client";

import { useDeferredValue, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileImage, FileText, Plus, Search, SearchX, Upload } from "lucide-react";
import { DocumentSummary, api } from "@/lib/api";
import { pages, relativeTime, statusBucket } from "@/lib/format";
import { useLocale } from "@/components/Providers";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, Page, PageHeader } from "@/components/app-shell/Page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";

type Filter = "all" | "processing" | "ready" | "attention";

export default function LibraryPage() {
  const { t, locale } = useLocale();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const search = useDeferredValue(query.trim());

  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ["documents", search],
    queryFn: () =>
      api<{ results: DocumentSummary[] }>(
        `/api/documents/${search ? `?q=${encodeURIComponent(search)}` : ""}`,
      ),
    // Documents move through the pipeline in the background, so the grid keeps
    // itself current without the user reloading.
    refetchInterval: 5000,
  });

  const all = data?.results ?? [];
  const counts = useMemo(() => {
    const result = { all: all.length, processing: 0, ready: 0, attention: 0 };
    for (const doc of all) {
      const bucket = statusBucket(doc.status);
      if (bucket !== "other") result[bucket] += 1;
    }
    return result;
  }, [all]);
  const documents = filter === "all" ? all : all.filter((doc) => statusBucket(doc.status) === filter);

  const label = (key: "filterAll" | "filterProcessing" | "filterReady" | "filterAttention", n: number) => (
    <>
      {t(key)}
      {n > 0 && <span className="tabular-nums text-muted-foreground">{n}</span>}
    </>
  );

  return (
    <Page>
      <PageHeader
        title={t("library")}
        description={t("librarySubtitle")}
        actions={
          <Button asChild>
            <Link href="/upload"><Plus /> {t("uploadDocument")}</Link>
          </Button>
        }
      />

      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative sm:max-w-xs sm:flex-1">
          <Search className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("searchDocuments")}
            className="ps-9"
            dir="auto"
            aria-label={t("searchDocuments")}
          />
        </div>
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:ms-auto sm:px-0">
          <Segmented<Filter>
            size="sm"
            value={filter}
            onChange={setFilter}
            aria-label={t("status")}
            options={[
              { value: "all", label: label("filterAll", counts.all) },
              { value: "processing", label: label("filterProcessing", counts.processing) },
              { value: "ready", label: label("filterReady", counts.ready) },
              { value: "attention", label: label("filterAttention", counts.attention) },
            ]}
          />
        </div>
      </div>

      {isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle />}
          title={t("loadFailed")}
          action={<Button variant="outline" onClick={() => refetch()}>{t("retry")}</Button>}
        />
      ) : all.length === 0 && !search ? (
        <EmptyState
          icon={<Upload />}
          title={t("noDocuments")}
          description={t("noDocumentsHint")}
          action={
            <Button asChild>
              <Link href="/upload"><Plus /> {t("uploadDocument")}</Link>
            </Button>
          }
        />
      ) : documents.length === 0 ? (
        <EmptyState icon={<SearchX />} title={t("noResults")} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {documents.map((doc) => (
            <DocumentCard key={doc.id} doc={doc} locale={locale} />
          ))}
        </div>
      )}
    </Page>
  );
}

function DocumentCard({ doc, locale }: { doc: DocumentSummary; locale: "ar" | "en" }) {
  const { t } = useLocale();
  const isImage = !doc.original_filename.toLowerCase().endsWith(".pdf");
  const Icon = isImage ? FileImage : FileText;

  return (
    <Link href={`/documents/${doc.id}`} className="group rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <Card className="flex h-full flex-col p-4 transition-all group-hover:border-foreground/20 group-hover:shadow-md">
        <div className="flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Icon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 text-sm font-medium leading-snug" dir="auto">
              {doc.display_title}
            </h2>
            <p className="mt-0.5 truncate text-xs text-muted-foreground" dir="auto">
              {doc.original_filename}
            </p>
          </div>
        </div>

        <p className="doc-text mt-3 line-clamp-2 min-h-[2lh] text-xs leading-relaxed text-muted-foreground" dir="auto">
          {doc.summary_short}
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
          <StatusBadge status={doc.status} />
          {doc.doc_type && <Badge variant="outline" className="capitalize">{doc.doc_type}</Badge>}
          {doc.page_count != null && <span>{pages(doc.page_count, t)}</span>}
          <span className="ms-auto">{relativeTime(doc.created_at, locale)}</span>
        </div>
      </Card>
    </Link>
  );
}
