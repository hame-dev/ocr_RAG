"use client";

import type { ReactNode } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useLocale } from "../Providers";

/** Full-size view of a chat image (an attachment or a generated chart). Closed when `image` is null. */
export function ImageLightbox({
  image, onClose, children,
}: {
  image: { src: string; title: string } | null;
  onClose: () => void;
  /** Extra controls under the image, e.g. a download link. */
  children?: ReactNode;
}) {
  const { t } = useLocale();

  return (
    <Dialog open={image !== null} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-4xl p-3" closeLabel={t("close")}>
        <DialogTitle className="truncate px-1 pe-10 text-sm font-medium" dir="auto">{image?.title}</DialogTitle>
        {image && (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
          <img src={image.src} alt={image.title} className="max-h-[75vh] w-full rounded-lg object-contain" />
        )}
        {children}
      </DialogContent>
    </Dialog>
  );
}
