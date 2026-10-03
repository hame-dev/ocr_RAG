"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Combine, Loader2, RotateCw, Sparkles, X } from "lucide-react";
import {
  ApiError, COLUMN_ROLES, COLUMN_TYPES, ColumnRole, ColumnType, SchemaColumn, SheetCard, SheetInfo,
  SheetSchema, confirmSheets, getSheets, previewSheet, reprofileSheet,
} from "@/lib/api";
import { fmt, num, StringKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useLocale } from "../Providers";

const TYPE_LABEL: Record<ColumnType, StringKey> = {
  text: "typeText", integer: "typeInteger", number: "typeNumber", date: "typeDate", boolean: "typeBoolean",
  category: "typeCategory", id: "typeId", email: "typeEmail", phone: "typePhone",
};
const ROLE_LABEL: Record<ColumnRole, StringKey> = {
  identifier: "roleIdentifier", title: "roleTitle", attribute: "roleAttribute", ignore: "roleIgnore",
};
// Where the columns can be edited: before the first confirm, and once indexed.
const EDITABLE = ["preprocessed", "ready"];
const PREVIEW_DELAY_MS = 400;

function schemaOf(sheet: SheetInfo): SheetSchema | null {
  const schema = sheet.schema_status === "confirmed" ? sheet.schema : sheet.proposed_schema;
  return schema && Array.isArray(schema.columns) ? (schema as SheetSchema) : null;
}

/**
 * The spreadsheet counterpart of Extract + Review: the AI's reading of every
 * column, editable, with a live preview of the record cards that get indexed.
 */
export function ColumnsStep({
  documentId, status, onConfirmed,
}: {
  documentId: string;
  status: string;
  onConfirmed: () => void;
}) {
  const { t } = useLocale();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["sheets", documentId],
    queryFn: () => getSheets(documentId),
    // Poll only while a proposal is still being written.
    refetchInterval: (query) =>
      query.state.data?.sheets.some((s) => s.schema_status === "pending" || s.schema_status === "proposing")
        ? 2000
        : false,
  });
  const sheets = data?.sheets ?? [];
  const [active, setActive] = useState(0);
  const [drafts, setDrafts] = useState<Record<number, SheetSchema>>({});
  // Which server version each draft was taken from; a new proposal replaces it.
  const loadedFrom = useRef<Record<number, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDrafts((current) => {
      let next = current;
      for (const sheet of sheets) {
        const schema = schemaOf(sheet);
        const version = `${sheet.schema_status}:${sheet.header_row}:${sheet.confirmed_at}`;
        if (schema && loadedFrom.current[sheet.index] !== version) {
          loadedFrom.current[sheet.index] = version;
          next = { ...next, [sheet.index]: schema };
        }
      }
      return next;
    });
  }, [sheets]);

  const editable = EDITABLE.includes(status);
  const confirmed = sheets.length > 0 && sheets.every((s) => s.schema_status === "confirmed");
  const ready = sheets.length > 0 && sheets.every((s) => drafts[s.index]);

  async function confirm() {
    setBusy(true);
    try {
      await confirmSheets(documentId, sheets.map((s) => ({ index: s.index, schema: drafts[s.index] })));
      toast.success(t("columnsConfirmed"));
      void queryClient.invalidateQueries({ queryKey: ["sheets", documentId] });
      void queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      onConfirmed();
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    } finally {
      setBusy(false);
    }
  }

  if (isLoading || (!sheets.length && ["uploaded", "preprocessing"].includes(status))) {
    return <Skeleton className="h-96 w-full rounded-xl" />;
  }
  const sheet = sheets.find((s) => s.index === active) ?? sheets[0];
  if (!sheet) return null;
  const draft = drafts[sheet.index];

  return (
    <div className="space-y-4">
      {sheets.length > 1 && (
        <div className="flex flex-wrap gap-1.5" role="tablist">
          {sheets.map((s) => (
            <Button
              key={s.index}
              role="tab"
              aria-selected={s.index === sheet.index}
              size="sm"
              variant={s.index === sheet.index ? "secondary" : "ghost"}
              onClick={() => setActive(s.index)}
            >
              <span dir="auto">{s.name}</span>
              <span className="tabular-nums text-muted-foreground">{num(s.row_count)}</span>
            </Button>
          ))}
        </div>
      )}

      {!editable && (
        <Alert><AlertDescription>{t("columnsLocked")}</AlertDescription></Alert>
      )}

      {draft ? (
        <SheetEditor
          key={sheet.index}
          documentId={documentId}
          sheet={sheet}
          draft={draft}
          disabled={!editable || busy}
          onChange={(schema) => setDrafts((current) => ({ ...current, [sheet.index]: schema }))}
          onReprofiled={() => void queryClient.invalidateQueries({ queryKey: ["sheets", documentId] })}
        />
      ) : (
        <Card>
          <CardContent className="flex items-center gap-3 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin text-primary" /> {t("columnsProposing")}
          </CardContent>
        </Card>
      )}

      <div className="flex justify-end">
        <Button onClick={() => void confirm()} disabled={!editable || !ready || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <Check />}
          {confirmed ? t("reconfirmColumns") : t("confirmColumns")}
        </Button>
      </div>
    </div>
  );
}

function SheetEditor({
  documentId, sheet, draft, disabled, onChange, onReprofiled,
}: {
  documentId: string;
  sheet: SheetInfo;
  draft: SheetSchema;
  disabled: boolean;
  onChange: (schema: SheetSchema) => void;
  onReprofiled: () => void;
}) {
  const { t } = useLocale();
  const profiles = useMemo(() => new Map(sheet.profile.map((p) => [p.header, p])), [sheet.profile]);
  const [preview, setPreview] = useState<SheetCard[]>(sheet.preview);
  const [headerRow, setHeaderRow] = useState(sheet.header_row ? String(sheet.header_row) : "");
  const [rereading, setRereading] = useState(false);

  // The preview is rendered by the server, exactly as indexing will.
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      previewSheet(documentId, sheet.index, draft)
        .then((result) => { if (!cancelled) setPreview(result.preview); })
        .catch(() => { /* keep the last preview */ });
    }, PREVIEW_DELAY_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [documentId, sheet.index, draft]);

  const setColumn = (index: number, patch: Partial<SchemaColumn>) =>
    onChange({ ...draft, columns: draft.columns.map((c, i) => (i === index ? { ...c, ...patch } : c)) });

  async function reread(row: number | null) {
    setRereading(true);
    try {
      await reprofileSheet(documentId, sheet.index, row);
      onReprofiled();
    } catch (error) {
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : undefined });
    } finally {
      setRereading(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <Card className="min-w-0">
        <CardHeader className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">{t("columnsTitle")}</CardTitle>
            {sheet.schema_source === "llm" && (
              <Badge variant="outline" className="gap-1"><Sparkles className="size-3" /> {t("columnsAiBadge")}</Badge>
            )}
            <span className="ms-auto text-xs text-muted-foreground tabular-nums">
              {fmt(t("columnsRows"), { n: num(sheet.row_count) })}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">{t("columnsIntro")}</p>
          {sheet.schema_source === "heuristic" && (
            <Alert><AlertDescription>{t("columnsHeuristic")}</AlertDescription></Alert>
          )}
        </CardHeader>

        <CardContent className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)_auto]">
            <div className="space-y-1.5">
              <Label htmlFor="entity">{t("entityLabel")}</Label>
              <Input
                id="entity" value={draft.entity} disabled={disabled} dir="auto"
                onChange={(e) => onChange({ ...draft, entity: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="title-template">{t("titleTemplateLabel")}</Label>
              <Input
                id="title-template" value={draft.title_template} disabled={disabled} dir="ltr"
                className="font-mono text-xs"
                onChange={(e) => onChange({ ...draft, title_template: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">{t("titleTemplateHint")}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="header-row">{t("headerRowLabel")}</Label>
              <div className="flex gap-1.5">
                <Input
                  id="header-row" type="number" min={1} className="w-20 tabular-nums" dir="ltr"
                  value={headerRow} disabled={disabled || rereading}
                  onChange={(e) => setHeaderRow(e.target.value)}
                />
                <Button
                  variant="outline" size="sm" className="h-9" disabled={disabled || rereading}
                  onClick={() => void reread(headerRow ? Number(headerRow) : null)}
                >
                  {rereading ? <Loader2 className="animate-spin" /> : <RotateCw />} {t("headerRowApply")}
                </Button>
              </div>
              <button
                type="button" disabled={disabled || rereading}
                className="text-xs text-muted-foreground underline-offset-2 hover:underline disabled:opacity-50"
                onClick={() => { setHeaderRow(""); void reread(null); }}
              >
                {t("headerRowNone")}
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-sm">
              <thead>
                <tr className="border-b text-start text-xs text-muted-foreground">
                  <th className="py-2 pe-3 text-start font-medium">{t("colOriginal")}</th>
                  <th className="py-2 pe-3 text-start font-medium">{t("colLabel")}</th>
                  <th className="py-2 pe-3 text-start font-medium">{t("colType")}</th>
                  <th className="py-2 pe-3 text-start font-medium">{t("colRole")}</th>
                  <th className="py-2 text-start font-medium">{t("colFillDown")}</th>
                </tr>
              </thead>
              <tbody>
                {draft.columns.map((column, index) => {
                  const profile = profiles.get(column.source);
                  return (
                    <tr key={column.source} className={cn("border-b align-top last:border-0", column.role === "ignore" && "opacity-60")}>
                      <td className="max-w-56 py-2.5 pe-3">
                        <p className="truncate font-medium" dir="auto" title={column.source}>{column.source}</p>
                        {profile && (
                          <p className="doc-text mt-0.5 line-clamp-2 text-xs text-muted-foreground" dir="auto">
                            {profile.samples.slice(0, 3).join(" · ")}
                          </p>
                        )}
                        {profile && profile.null_ratio > 0 && (
                          <p className="mt-0.5 text-xs text-muted-foreground tabular-nums">
                            {fmt(t("colEmpty"), { p: num(profile.null_ratio * 100) })}
                          </p>
                        )}
                      </td>
                      <td className="py-2 pe-3">
                        <Input
                          value={column.label} disabled={disabled} dir="auto" className="h-8"
                          aria-label={`${t("colLabel")}: ${column.source}`}
                          onChange={(e) => setColumn(index, { label: e.target.value })}
                        />
                        <p className="mt-1 font-mono text-[11px] text-muted-foreground" dir="ltr">{column.key}</p>
                      </td>
                      <td className="py-2 pe-3">
                        <Select
                          value={column.type} disabled={disabled}
                          onValueChange={(value) => setColumn(index, { type: value as ColumnType })}
                        >
                          <SelectTrigger className="h-8 w-36" aria-label={`${t("colType")}: ${column.source}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {COLUMN_TYPES.map((type) => (
                              <SelectItem key={type} value={type}>{t(TYPE_LABEL[type])}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </td>
                      <td className="py-2 pe-3">
                        <Select
                          value={column.role} disabled={disabled}
                          onValueChange={(value) => setColumn(index, { role: value as ColumnRole })}
                        >
                          <SelectTrigger className="h-8 w-32" aria-label={`${t("colRole")}: ${column.source}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {COLUMN_ROLES.map((role) => (
                              <SelectItem key={role} value={role}>{t(ROLE_LABEL[role])}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </td>
                      <td className="py-3">
                        <Checkbox
                          checked={column.fill_down} disabled={disabled}
                          aria-label={`${t("colFillDown")}: ${column.source}`}
                          title={t("colFillDownHint")}
                          onCheckedChange={(checked) => setColumn(index, { fill_down: checked === true })}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <CombineEditor draft={draft} disabled={disabled} onChange={onChange} />
        </CardContent>
      </Card>

      <Card className="h-fit lg:sticky lg:top-4">
        <CardHeader>
          <CardTitle className="text-base">{t("previewTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {preview.map((card) => (
            <pre
              key={card.row} dir="auto"
              className="doc-text whitespace-pre-wrap rounded-lg border bg-muted/40 p-3 text-xs leading-relaxed"
            >
              {card.text}
            </pre>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function CombineEditor({
  draft, disabled, onChange,
}: {
  draft: SheetSchema;
  disabled: boolean;
  onChange: (schema: SheetSchema) => void;
}) {
  const { t } = useLocale();
  const [picked, setPicked] = useState<string[]>([]);
  const [name, setName] = useState("");
  const labels = new Map(draft.columns.map((c) => [c.key, c.label]));

  function add() {
    const key = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
      || picked.join("_");
    // The server normalizes keys again (unique, SQL-safe); this is only a suggestion.
    onChange({
      ...draft,
      combine: [...draft.combine, { key, label: name.trim() || picked.map((k) => labels.get(k)).join(" "), from: picked, sep: " " }],
    });
    setPicked([]);
    setName("");
  }

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div>
        <p className="flex items-center gap-1.5 text-sm font-medium"><Combine className="size-4" /> {t("combineTitle")}</p>
        <p className="text-xs text-muted-foreground">{t("combineHint")}</p>
      </div>

      {draft.combine.length > 0 && (
        <ul className="space-y-1.5">
          {draft.combine.map((item, index) => (
            <li key={`${item.key}-${index}`} className="flex items-center gap-2 text-sm">
              <span className="font-medium" dir="auto">{item.label}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" dir="auto">
                = {item.from.map((k) => labels.get(k) ?? k).join(" + ")}
              </span>
              <Button
                variant="ghost" size="icon" className="size-7" disabled={disabled} aria-label={t("combineRemove")}
                onClick={() => onChange({ ...draft, combine: draft.combine.filter((_, i) => i !== index) })}
              >
                <X />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-1.5">
        {draft.columns.filter((c) => c.role !== "ignore").map((column) => {
          const on = picked.includes(column.key);
          return (
            <button
              key={column.key} type="button" disabled={disabled} aria-pressed={on}
              onClick={() => setPicked((current) => (on ? current.filter((k) => k !== column.key) : [...current, column.key]))}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:opacity-50",
                on ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-accent",
              )}
            >
              <span dir="auto">{column.label}</span>
            </button>
          );
        })}
      </div>
      <div className="flex gap-2">
        <Input
          value={name} placeholder={t("combineName")} disabled={disabled} dir="auto" className="h-8"
          onChange={(e) => setName(e.target.value)}
        />
        <Button size="sm" variant="outline" disabled={disabled || picked.length < 2} onClick={add}>
          {t("combineAdd")}
        </Button>
      </div>
    </div>
  );
}
