"use client";

import { useDeferredValue, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, FileText, Library, Loader2, Paperclip, Search } from "lucide-react";
import { api, DocumentSummary, SelectedDocument } from "@/lib/api";
import { fmt } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useLocale } from "./Providers";

interface SourcePickerProps {
  selected: SelectedDocument[];
  disabled?: boolean;
  onChange: (documents: SelectedDocument[]) => Promise<void> | void;
}

/** Scope a conversation to specific documents (or the whole library). */
export function SourcePicker({ selected, disabled, onChange }: SourcePickerProps) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const search = useDeferredValue(query.trim());

  const { data, isPending } = useQuery({
    queryKey: ["chat-source-documents", search],
    queryFn: () =>
      api<{ results: DocumentSummary[] }>(
        `/api/documents/?status__in=ready,indexed&limit=50${search ? `&q=${encodeURIComponent(search)}` : ""}`,
      ),
    enabled: open,
  });

  const selectedIds = new Set(selected.map((document) => document.id));
  const documents = data?.results ?? [];

  function toggle(document: DocumentSummary) {
    const compact: SelectedDocument = {
      id: document.id,
      display_title: document.display_title,
      original_filename: document.original_filename,
    };
    void onChange(
      selectedIds.has(document.id)
        ? selected.filter((item) => item.id !== document.id)
        : [...selected, compact],
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={cn("h-8 gap-1.5 text-muted-foreground", selected.length && "text-foreground")}
        >
          <Paperclip />
          {selected.length ? fmt(t("selectedSourceCount"), { count: selected.length }) : t("sources")}
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" className="w-[min(24rem,calc(100vw-2rem))] p-0">
        <div className="border-b p-3">
          <p className="text-sm font-semibold">{t("selectSources")}</p>
          <p className="text-xs text-muted-foreground">{t("sourcePickerHint")}</p>
          <div className="relative mt-3">
            <Search className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("searchSources")}
              className="h-8 ps-8"
              dir="auto"
            />
          </div>
        </div>

        <div className="scrollbar-thin max-h-72 overflow-y-auto p-1.5">
          <button
            type="button"
            onClick={() => void onChange([])}
            className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-start text-sm hover:bg-accent"
          >
            <Library className="size-4 text-muted-foreground" />
            <span className="flex-1">{t("wholeLibrary")}</span>
            {!selected.length && <Check className="size-4 text-primary" />}
          </button>
          <div className="my-1 h-px bg-border" />
          {isPending ? (
            <div className="flex justify-center py-6"><Loader2 className="size-4 animate-spin text-muted-foreground" /></div>
          ) : documents.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">{t("noMatchingSources")}</p>
          ) : (
            documents.map((document) => {
              const checked = selectedIds.has(document.id);
              return (
                <button
                  key={document.id}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={checked}
                  onClick={() => toggle(document)}
                  className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-start hover:bg-accent"
                >
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm" dir="auto">{document.display_title}</span>
                    {document.display_title !== document.original_filename && (
                      <span className="block truncate text-xs text-muted-foreground" dir="auto">
                        {document.original_filename}
                      </span>
                    )}
                  </span>
                  <span className={cn(
                    "grid size-4 shrink-0 place-items-center rounded border",
                    checked ? "border-primary bg-primary text-primary-foreground" : "border-input",
                  )}>
                    {checked && <Check className="size-3" strokeWidth={3} />}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
