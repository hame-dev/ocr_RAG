import { LOCALE_KEY } from "./i18n";

export type ThemeMode = "system" | "light" | "dark";
export type Accent = "brand" | "blue" | "violet" | "emerald" | "amber" | "rose" | "teal";

export const THEME_MODES: ThemeMode[] = ["system", "light", "dark"];

/** Swatch values are the light-mode hex from globals.css, for the picker chips. */
export const ACCENTS: { name: Accent; swatch: string; label_en: string; label_ar: string }[] = [
  // Matches the logo's primary blue (#014f7b).
  { name: "brand", swatch: "#014f7b", label_en: "Sovereign", label_ar: "السيادي" },
  { name: "blue", swatch: "#2563eb", label_en: "Blue", label_ar: "أزرق" },
  { name: "violet", swatch: "#7c3aed", label_en: "Violet", label_ar: "بنفسجي" },
  { name: "emerald", swatch: "#059669", label_en: "Emerald", label_ar: "أخضر" },
  { name: "amber", swatch: "#b45309", label_en: "Amber", label_ar: "كهرماني" },
  { name: "rose", swatch: "#e11d48", label_en: "Rose", label_ar: "وردي" },
  { name: "teal", swatch: "#0d9488", label_en: "Teal", label_ar: "فيروزي" },
];

export const DEFAULT_MODE: ThemeMode = "system";
export const DEFAULT_ACCENT: Accent = "brand";

export const MODE_KEY = "theme-mode";
export const ACCENT_KEY = "theme-accent";

const isMode = (v: unknown): v is ThemeMode =>
  typeof v === "string" && (THEME_MODES as string[]).includes(v);

const isAccent = (v: unknown): v is Accent =>
  typeof v === "string" && ACCENTS.some((a) => a.name === v);

export function readStoredTheme(): { mode: ThemeMode; accent: Accent } {
  if (typeof window === "undefined") return { mode: DEFAULT_MODE, accent: DEFAULT_ACCENT };
  let mode: unknown, accent: unknown;
  try {
    mode = localStorage.getItem(MODE_KEY);
    accent = localStorage.getItem(ACCENT_KEY);
  } catch {
    /* private browsing / storage disabled */
  }
  return {
    mode: isMode(mode) ? mode : DEFAULT_MODE,
    accent: isAccent(accent) ? accent : DEFAULT_ACCENT,
  };
}

/**
 * Write the theme onto <html>. "system" removes data-theme entirely so the
 * prefers-color-scheme media query in globals.css takes over.
 */
export function applyTheme(mode: ThemeMode, accent: Accent) {
  const root = document.documentElement;
  if (mode === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
  root.setAttribute("data-accent", accent);
}

/**
 * Runs before first paint (injected as a blocking inline script) so a dark-theme
 * user never sees a white flash, and an Arabic user never sees the page flip
 * from LTR to RTL, while React hydrates.
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var l=localStorage.getItem(${JSON.stringify(LOCALE_KEY)});
if(l==="ar"||l==="en"){document.documentElement.lang=l;document.documentElement.dir=l==="ar"?"rtl":"ltr";}
var m=localStorage.getItem(${JSON.stringify(MODE_KEY)});
var a=localStorage.getItem(${JSON.stringify(ACCENT_KEY)});
var r=document.documentElement;
if(m==="light"||m==="dark")r.setAttribute("data-theme",m);
if(a)r.setAttribute("data-accent",a);
}catch(e){}})();`;
