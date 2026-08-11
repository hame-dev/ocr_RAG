"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLocale } from "./Providers";

export function Shell({ children }: { children: React.ReactNode }) {
  const { t, locale, setLocale } = useLocale();
  const pathname = usePathname();

  if (pathname.startsWith("/chat")) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen">
      <header
        className="sticky top-0 z-20 border-b backdrop-blur"
        style={{ borderColor: "var(--border)", background: "var(--bg)" }}
      >
        {/* ps/pe and ms/me are logical properties: they flip automatically in RTL. */}
        <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <Link href="/" className="font-semibold">
            {t("appName")}
          </Link>
          <Link href="/" className="text-sm opacity-80 hover:opacity-100">
            {t("library")}
          </Link>
          <Link href="/upload" className="text-sm opacity-80 hover:opacity-100">
            {t("upload")}
          </Link>
          <Link href="/chat" className="text-sm opacity-80 hover:opacity-100">
            {t("chat")}
          </Link>
          <Link href="/settings" className="text-sm opacity-80 hover:opacity-100">
            {t("settings")}
          </Link>

          <button
            onClick={() => setLocale(locale === "ar" ? "en" : "ar")}
            className="ms-auto rounded-md border px-3 py-1 text-sm"
            style={{ borderColor: "var(--border)" }}
          >
            {locale === "ar" ? "English" : "العربية"}
          </button>
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
