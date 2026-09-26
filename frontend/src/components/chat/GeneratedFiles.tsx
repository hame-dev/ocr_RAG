"use client";

import { useState } from "react";
import { Download, FileSpreadsheet, FileText, Presentation, type LucideIcon } from "lucide-react";
import { GeneratedFile, generatedFileUrl } from "@/lib/api";
import { bytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLocale } from "../Providers";
import { ImageLightbox } from "./ImageLightbox";

const ICONS: Record<GeneratedFile["kind"], LucideIcon> = {
  image: FileText,
  document: FileText,
  spreadsheet: FileSpreadsheet,
  presentation: Presentation,
  data: FileText,
};

/** Files General-mode code produced: charts inline (open a lightbox), everything else as downloads. */
export function GeneratedFiles({ files, className }: { files: GeneratedFile[]; className?: string }) {
  const { t } = useLocale();
  const [open, setOpen] = useState<GeneratedFile | null>(null);
  if (!files.length) return null;

  const images = files.filter((file) => file.inline);
  const downloads = files.filter((file) => !file.inline);

  return (
    <div className={cn("space-y-3", className)}>
      {images.map((file) => (
        <button
          key={file.id}
          type="button"
          onClick={() => setOpen(file)}
          className="block max-w-full overflow-hidden rounded-xl border bg-white transition-opacity hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`${t("openImage")}: ${file.filename}`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
          <img src={generatedFileUrl(file)} alt={file.filename} className="max-h-[28rem] w-auto" loading="lazy" />
        </button>
      ))}

      {downloads.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {downloads.map((file) => {
            const Icon = ICONS[file.kind] ?? FileText;
            return (
              <a
                key={file.id}
                href={generatedFileUrl(file)}
                download={file.filename}
                className="group/file flex max-w-72 items-center gap-2.5 rounded-xl border bg-card p-2 pe-3 transition-colors hover:border-primary/40 hover:bg-accent/50"
              >
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium" dir="auto">{file.filename}</span>
                  <span className="block text-[11px] text-muted-foreground">{bytes(file.size)}</span>
                </span>
                <Download
                  className="size-4 shrink-0 text-muted-foreground transition-colors group-hover/file:text-primary"
                  aria-label={t("download")}
                />
              </a>
            );
          })}
        </div>
      )}

      <ImageLightbox
        image={open && { src: generatedFileUrl(open), title: open.filename }}
        onClose={() => setOpen(null)}
      >
        {open && (
          <a
            href={generatedFileUrl(open)}
            // Cross-origin, so `download` is ignored for an inline image: open it instead.
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 self-start px-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <Download className="size-3.5" /> {t("download")}
          </a>
        )}
      </ImageLightbox>
    </div>
  );
}
