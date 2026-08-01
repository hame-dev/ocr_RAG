"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { useLocale } from "@/components/Providers";

export default function UploadPage() {
  const { t } = useLocale();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  async function send(file: File) {
    setBusy(true);
    setError(null);
    try {
      const doc = await uploadDocument(file);
      router.push(`/documents/${doc.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-5 text-xl font-semibold">{t("upload")}</h1>

      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void send(file);
        }}
        className="card cursor-pointer p-12 text-center transition-colors"
        style={{ borderColor: dragging ? "var(--accent)" : "var(--border)" }}
      >
        {busy ? (
          <div className="flex items-center justify-center gap-3">
            <span
              className="inline-block h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent"
              style={{ color: "var(--accent)" }}
            />
            <span>{t("processing")}</span>
          </div>
        ) : (
          <>
            <p className="mb-1 font-medium">{t("dropHere")}</p>
            <p className="text-sm" style={{ color: "var(--muted)" }}>{t("supportedFiles")}</p>
          </>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept=".pdf,image/*"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void send(file);
        }}
      />

      {error && (
        <p className="mt-3 rounded-md p-3 text-sm" style={{ background: "#fee2e2", color: "#991b1b" }}>
          {error}
        </p>
      )}
    </div>
  );
}
