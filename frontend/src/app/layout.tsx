import type { Metadata } from "next";
import { IBM_Plex_Sans_Arabic, Noto_Naskh_Arabic } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { Shell } from "@/components/Shell";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

// Latin and Arabic metrics genuinely match in IBM Plex Sans Arabic, which is
// rare and matters for bilingual tables.
const ui = IBM_Plex_Sans_Arabic({
  subsets: ["arabic", "latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-ui",
});

// Naskh is far more readable than a UI face for long transcribed passages.
const doc = Noto_Naskh_Arabic({
  subsets: ["arabic", "latin"],
  weight: ["400", "500", "700"],
  variable: "--font-doc",
});

export const metadata: Metadata = {
  title: "Sovereign AI",
  description: "Read, correct and chat with Arabic and English documents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // English is the default locale; the head script switches to the stored
  // choice (e.g. Arabic/RTL) before first paint. suppressHydrationWarning
  // covers that deliberate lang/dir difference from the server HTML.
  return (
    <html lang="en" dir="ltr" className={`${ui.variable} ${doc.variable}`} suppressHydrationWarning>
      <head>
        {/* Blocking, so the stored theme lands on <html> before first paint and
            a dark-theme user never sees a white flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="font-sans antialiased">
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
