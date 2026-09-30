"use client";

import { AlertTriangle, FileClock, Loader2, Pencil, RotateCw, Sparkles } from "lucide-react";
import { humanizeKey } from "@/lib/format";
import { StringKey } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "../app-shell/Page";
import { useLocale } from "../Providers";

/* eslint-disable @typescript-eslint/no-explicit-any -- metadata is a schema-driven JSON record */

const WORKING = ["text_finalized", "enriching"];

export function DetailsStep({
  status, metadata, plan, onRetry,
}: {
  status: string;
  metadata: any;
  plan: any;
  onRetry: () => Promise<void>;
}) {
  const { t, locale } = useLocale();

  if (!metadata) {
    if (WORKING.includes(status)) {
      return (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Loader2 className="size-4 animate-spin text-primary" /> {t("metadataExtracting")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="space-y-2">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-4 w-4/5" />
              </div>
            ))}
          </CardContent>
        </Card>
      );
    }
    if (status === "failed") {
      return (
        <EmptyState
          icon={<AlertTriangle />}
          title={t("actionFailed")}
          action={<Button variant="outline" onClick={() => void onRetry()}><RotateCw /> {t("retryProcessing")}</Button>}
        />
      );
    }
    return <EmptyState icon={<FileClock />} title={t("stepDetails")} description={t("metadataPending")} />;
  }

  const fields: [StringKey, any, boolean?][] = [
    ["fieldType", metadata.doc_type],
    ["fieldTitle", metadata.title, true],
    ["fieldTitleLatin", metadata.title_translit],
    ["fieldLanguage", [metadata.primary_language, ...(metadata.languages ?? [])].filter(Boolean)
      .filter((v: string, i: number, a: string[]) => a.indexOf(v) === i).join(", ").toUpperCase()],
    ["fieldDate", metadata.document_date],
    ["fieldReference", metadata.identifiers?.ref_numbers],
    ["fieldAmounts", metadata.identifiers?.amounts, true],
    ["fieldPeople", metadata.entities?.persons, true],
    ["fieldOrganizations", metadata.entities?.organizations, true],
  ];
  const custom = Object.entries(metadata.custom_fields ?? {}).filter(([, value]) => value != null && value !== "");

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        {metadata.summary_short && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Sparkles className="size-4 text-primary" /> {t("summary")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="doc-text text-[15px]" dir="auto">{metadata.summary_short}</p>
            </CardContent>
          </Card>
        )}

        {custom.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{t("documentFields")}</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="divide-y">
                {custom.map(([key, value]) => (
                  <div key={key} className="grid gap-1 py-2.5 sm:grid-cols-[minmax(0,14rem)_1fr] sm:gap-4">
                    <dt className="text-sm text-muted-foreground">{humanizeKey(key)}</dt>
                    <dd className="doc-text text-sm" dir="auto">{format(value)}</dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        )}
      </div>

      <Card className="h-fit">
        <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
          <CardTitle className="text-base">{t("metadata")}</CardTitle>
          <div className="ms-auto flex gap-1.5">
            {metadata.is_partial && <Badge variant="warning">{t("partial")}</Badge>}
            {metadata.human_edited && <Badge variant="secondary"><Pencil /> {t("editedByYou")}</Badge>}
          </div>
        </CardHeader>
        <CardContent>
          <dl className="space-y-3.5">
            {fields.map(([label, value, rtlAware]) => {
              const text = format(value);
              if (!text) return null;
              return (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{t(label)}</dt>
                  <dd className={rtlAware ? "doc-text mt-0.5 text-sm" : "mt-0.5 text-sm capitalize"} dir={rtlAware ? "auto" : undefined}>
                    {text}
                  </dd>
                </div>
              );
            })}
            {metadata.keywords?.length > 0 && (
              <div>
                <dt className="mb-1.5 text-xs text-muted-foreground">{t("fieldKeywords")}</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {metadata.keywords.map((keyword: string) => (
                    <Badge key={keyword} variant="secondary" className="doc-text font-normal" dir="auto">{keyword}</Badge>
                  ))}
                </dd>
              </div>
            )}
            {metadata.quality_flags?.length > 0 && (
              <div>
                <dt className="mb-1.5 text-xs text-muted-foreground">{t("qualityFlags")}</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {metadata.quality_flags.map((flag: string) => (
                    <Badge key={flag} variant="warning">{humanizeKey(flag)}</Badge>
                  ))}
                </dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {plan?.proposed_fields?.length > 0 && !custom.length && (
        <Card className="lg:col-span-3">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">{t("proposedFields")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {plan.proposed_fields.map((field: any) => (
              <div key={field.key} className="rounded-lg border p-3">
                <p className="text-sm font-medium">{(locale === "ar" ? field.label_ar : field.label_en) ?? field.label_en ?? humanizeKey(field.key)}</p>
                <p className="doc-text text-xs text-muted-foreground" dir="auto">{field.example_value}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function format(value: any): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.filter(Boolean).join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
