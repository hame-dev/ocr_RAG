"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { DocumentSummary, listDocuments } from "@/lib/api";
import { num } from "@/lib/i18n";
import { useLocale } from "@/components/Providers";
import { StatusBadge } from "@/components/StatusBadge";

export default function LibraryPage() {
  const { t } = useLocale();
  const { data, isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    // Documents move through the pipeline in the background, so the grid keeps
    // itself current without the user reloading.
    refetchInterval: 5000,
  });

  const documents: DocumentSummary[] = data?.results ?? [];

  return (
    <div>
      <div className="mb-5 flex items-center">
        <h1 className="text-xl font-semibold">{t("library")}</h1>
        <Link
          href="/upload"
          className="ms-auto rounded-md px-3 py-1.5 text-sm text-white"
          style={{ background: "var(--accent)" }}
        >
          {t("upload")}
        </Link>
      </div>

      {isLoading && <p style={{ color: "var(--muted)" }}>…</p>}

      {!isLoading && !documents.length && (
        <div className="card p-10 text-center" style={{ color: "var(--muted)" }}>
          <p>{t("noDocuments")}</p>
          <Link href="/upload" className="mt-3 inline-block underline">
            {t("upload")}
          </Link>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {documents.map((doc) => (
          <Link key={doc.id} href={`/documents/${doc.id}`} className="card block p-4 hover:opacity-90">
            <div className="mb-2 flex items-start gap-2">
              <h2 className="doc-text line-clamp-2 flex-1 text-sm font-medium" dir="auto">
                {doc.display_title}
              </h2>
              <StatusBadge status={doc.status} />
            </div>

            {doc.summary_short && (
              <p className="doc-text line-clamp-3 text-xs" dir="auto" style={{ color: "var(--muted)" }}>
                {doc.summary_short}
              </p>
            )}

            <div className="mt-3 flex items-center gap-2 text-xs" style={{ color: "var(--muted)" }}>
              {doc.doc_type && <span className="rounded bg-black/5 px-1.5 py-0.5">{doc.doc_type}</span>}
              {doc.page_count != null && (
                <span className="numeric">
                  {num(doc.page_count)} {t("page")}
                </span>
              )}
              {doc.detected_languages?.map((l) => (
                <span key={l} className="uppercase">{l}</span>
              ))}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
