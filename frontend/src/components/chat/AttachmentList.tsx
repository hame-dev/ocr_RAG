"use client";

import { useState } from "react";
import { FileText } from "lucide-react";
import { ChatAttachment, attachmentPreviewUrl } from "@/lib/api";
import { bytes, pages } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLocale } from "../Providers";
import { ImageLightbox } from "./ImageLightbox";

/** Files sent with a user message: image thumbnails (open a lightbox) and file chips. */
export function AttachmentList({ attachments, className }: { attachments: ChatAttachment[]; className?: string }) {
  const { t } = useLocale();
  const [open, setOpen] = useState<ChatAttachment | null>(null);
  if (!attachments.length) return null;

  return (
    <div className={cn("flex flex-wrap justify-end gap-2", className)}>
      {attachments.map((attachment) => {
        const preview = attachmentPreviewUrl(attachment);
        return attachment.kind === "image" && preview ? (
          <button
            key={attachment.id}
            type="button"
            onClick={() => setOpen(attachment)}
            className="overflow-hidden rounded-xl border bg-muted transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`${t("openImage")}: ${attachment.filename}`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
            <img src={preview} alt={attachment.filename} className="size-24 object-cover" loading="lazy" />
          </button>
        ) : (
          <button
            key={attachment.id}
            type="button"
            onClick={() => preview && setOpen(attachment)}
            className={cn(
              "flex max-w-60 items-center gap-2.5 rounded-xl border bg-card p-2 pe-3 text-start",
              preview ? "hover:bg-accent/50" : "cursor-default",
            )}
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
              <FileText className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium" dir="auto">{attachment.filename}</span>
              <span className="block text-[11px] text-muted-foreground">
                {attachment.pages ? pages(attachment.pages, t) : bytes(attachment.size)}
              </span>
            </span>
          </button>
        );
      })}

      <ImageLightbox
        image={open && attachmentPreviewUrl(open) ? { src: attachmentPreviewUrl(open)!, title: open.filename } : null}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}
