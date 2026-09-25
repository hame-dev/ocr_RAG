"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { DEFAULT_LOCALE, LOCALE_KEY, Locale, StringKey, dirFor, isLocale, t } from "@/lib/i18n";
import {
  Accent, ACCENT_KEY, applyTheme, DEFAULT_ACCENT, DEFAULT_MODE, MODE_KEY,
  readStoredTheme, ThemeMode,
} from "@/lib/theme";
import { DirectionProvider } from "@radix-ui/react-direction";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "./AuthProvider";

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

interface ThemeCtx {
  mode: ThemeMode;
  accent: Accent;
  setMode: (m: ThemeMode) => void;
  setAccent: (a: Accent) => void;
}

const ThemeContext = createContext<ThemeCtx>({
  mode: DEFAULT_MODE,
  accent: DEFAULT_ACCENT,
  setMode: () => {},
  setAccent: () => {},
});

export const useTheme = () => useContext(ThemeContext);

function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Server renders the defaults; the inline head script has already applied the
  // stored values to <html>, and this effect syncs React state to match.
  const [mode, setModeState] = useState<ThemeMode>(DEFAULT_MODE);
  const [accent, setAccentState] = useState<Accent>(DEFAULT_ACCENT);

  useEffect(() => {
    const stored = readStoredTheme();
    setModeState(stored.mode);
    setAccentState(stored.accent);
    applyTheme(stored.mode, stored.accent);
  }, []);

  const value = useMemo<ThemeCtx>(
    () => ({
      mode,
      accent,
      setMode: (m) => {
        setModeState(m);
        applyTheme(m, accent);
        try { localStorage.setItem(MODE_KEY, m); } catch {}
      },
      setAccent: (a) => {
        setAccentState(a);
        applyTheme(mode, a);
        try { localStorage.setItem(ACCENT_KEY, a); } catch {}
      },
    }),
    [mode, accent],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  // Server renders the default; the stored choice (already applied to <html>
  // by the head script) is restored on mount.
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } }),
  );

  useEffect(() => {
    try {
      const stored = localStorage.getItem(LOCALE_KEY);
      if (isLocale(stored)) setLocaleState(stored);
    } catch {
      /* storage unavailable: keep the default */
    }
  }, []);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try { localStorage.setItem(LOCALE_KEY, next); } catch {}
  }, []);

  // Keep the document element in sync so CSS logical properties resolve
  // correctly for the whole tree.
  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = dirFor(locale);
  }, [locale]);

  const value = useMemo<LocaleCtx>(
    () => ({ locale, setLocale, t: (k) => t(locale, k), dir: dirFor(locale) }),
    [locale, setLocale],
  );

  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <Ctx.Provider value={value}>
          {/* Radix menus, selects and sheets read reading direction from here. */}
          <DirectionProvider dir={value.dir}>
            <TooltipProvider delayDuration={300}>
              <AuthProvider>{children}</AuthProvider>
              <Toaster />
            </TooltipProvider>
          </DirectionProvider>
        </Ctx.Provider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
