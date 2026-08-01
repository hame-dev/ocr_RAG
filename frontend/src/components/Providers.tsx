"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { DEFAULT_LOCALE, Locale, StringKey, dirFor, t } from "@/lib/i18n";

interface LocaleCtx {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: StringKey) => string;
  dir: "rtl" | "ltr";
}

const Ctx = createContext<LocaleCtx>({
  locale: DEFAULT_LOCALE,
  setLocale: () => {},
  t: (k) => t(DEFAULT_LOCALE, k),
  dir: "rtl",
});

export const useLocale = () => useContext(Ctx);

export function Providers({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>(DEFAULT_LOCALE);
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } }),
  );

  // Keep the document element in sync so CSS logical properties resolve
  // correctly for the whole tree.
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = dirFor(locale);
  }, [locale]);

  const value = useMemo<LocaleCtx>(
    () => ({ locale, setLocale, t: (k) => t(locale, k), dir: dirFor(locale) }),
    [locale],
  );

  return (
    <QueryClientProvider client={client}>
      <Ctx.Provider value={value}>{children}</Ctx.Provider>
    </QueryClientProvider>
  );
}
