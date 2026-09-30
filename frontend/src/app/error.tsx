"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { useLocale } from "@/components/Providers";

/**
 * Route-level error boundary. Without it any render error replaced the whole
 * app with Next's default error screen; this keeps the shell and offers a retry.
 */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const { t } = useLocale();

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-full min-h-[50vh] items-center justify-center p-6">
      <div className="max-w-sm space-y-3 text-center" role="alert">
        <h1 className="text-lg font-semibold">{t("loadFailed")}</h1>
        <Button variant="outline" onClick={reset}>{t("retry")}</Button>
      </div>
    </div>
  );
}
