"use client";

import { Toaster as Sonner } from "sonner";
import { useLocale, useTheme } from "@/components/Providers";

/** Toasts follow the app's theme and reading direction. */
export function Toaster() {
  const { mode } = useTheme();
  const { dir } = useLocale();
  return (
    <Sonner
      theme={mode}
      dir={dir}
      position={dir === "rtl" ? "bottom-left" : "bottom-right"}
      closeButton
      toastOptions={{
        classNames: {
          toast: "!rounded-xl !border-border !bg-popover !text-popover-foreground !shadow-lg",
          description: "!text-muted-foreground",
        },
      }}
    />
  );
}
