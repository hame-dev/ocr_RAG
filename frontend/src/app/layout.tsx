import type { Metadata } from "next";
import { IBM_Plex_Sans_Arabic, Noto_Naskh_Arabic } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { Shell } from "@/components/Shell";

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
  title: "Document Reader — OCR + RAG",
  description: "Read, correct and chat with Arabic and English documents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Arabic is the default locale, so the app is RTL out of the box.
  return (
    <html lang="ar" dir="rtl" className={`${ui.variable} ${doc.variable}`}>
      <body className="font-sans antialiased">
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
