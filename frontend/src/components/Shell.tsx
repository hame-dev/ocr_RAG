"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu } from "lucide-react";
import Link from "next/link";
import { loginUrl } from "@/lib/redirect";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { AppSidebar, BrandMark } from "./app-shell/AppSidebar";
import { useAuth } from "./AuthProvider";
import { useLocale } from "./Providers";

const COLLAPSED_KEY = "ocr-rag-sidebar-collapsed";

export function Shell({ children }: { children: React.ReactNode }) {
  const { t } = useLocale();
  const { status, retry } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // UX only: the API rejects every request without a session regardless.
  useEffect(() => {
    if (status === "anonymous" && !isLogin) {
      router.replace(loginUrl(pathname, window.location.search));
    }
  }, [status, isLogin, pathname, router]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSED_KEY) === "true");
    } catch {
      /* storage unavailable */
    }
  }, []);

  // Close the mobile drawer on navigation.
  useEffect(() => setMobileOpen(false), [pathname]);

  function toggleCollapsed() {
    setCollapsed((current) => {
      try { localStorage.setItem(COLLAPSED_KEY, String(!current)); } catch {}
      return !current;
    });
  }

  if (isLogin) return <>{children}</>;
  if (status === "unreachable") {
    // An API outage must not look like being signed out (and bounce to /login).
    return (
      <div className="flex h-dvh items-center justify-center bg-background p-6">
        <div className="max-w-sm space-y-3 text-center" role="alert">
          <h1 className="text-lg font-semibold">{t("apiUnreachable")}</h1>
          <p className="text-sm text-muted-foreground">{t("apiUnreachableHint")}</p>
          <Button variant="outline" onClick={retry}>{t("retry")}</Button>
        </div>
      </div>
    );
  }
  if (status !== "authenticated") return <ShellSkeleton />;

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:start-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:shadow"
      >
        {t("skipToContent")}
      </a>
      <aside
        className={`hidden shrink-0 border-e bg-sidebar transition-[width] duration-200 lg:block ${
          collapsed ? "w-[60px]" : "w-64"
        }`}
      >
        <AppSidebar collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent>
          <SheetTitle className="sr-only">{t("appNavigation")}</SheetTitle>
          <AppSidebar onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setMobileOpen(true)} aria-label={t("openMenu")}>
            <Menu />
          </Button>
          <Link href="/" className="flex items-center gap-2">
            <BrandMark className="size-7" />
            <span className="text-sm font-semibold tracking-tight">{t("appName")}</span>
          </Link>
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}

function ShellSkeleton() {
  return (
    <div className="flex h-dvh bg-background" aria-busy="true">
      <div className="hidden w-64 shrink-0 space-y-3 border-e bg-sidebar p-3 lg:block">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-9 w-full" />
        <div className="space-y-2 pt-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
        </div>
      </div>
      <div className="flex-1 space-y-4 p-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
        <div className="grid gap-4 pt-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36 w-full rounded-xl" />)}
        </div>
      </div>
    </div>
  );
}
