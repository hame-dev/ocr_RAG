"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, createConversation } from "@/lib/api";
import { useLocale } from "@/components/Providers";

export default function ChatIndexPage() {
  const { t } = useLocale();
  const router = useRouter();
  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<any>("/api/conversations/"),
  });

  async function startNew() {
    const conversation = await createConversation("all");
    router.push(`/chat/${conversation.id}`);
  }

  return (
    <div>
      <div className="mb-5 flex items-center">
        <h1 className="text-xl font-semibold">{t("chat")}</h1>
        <button
          onClick={startNew}
          className="ms-auto rounded-md px-3 py-1.5 text-sm text-white"
          style={{ background: "var(--accent)" }}
        >
          + {t("chat")}
        </button>
      </div>

      <ul className="space-y-2">
        {(data?.results ?? []).map((c: any) => (
          <li key={c.id}>
            <Link href={`/chat/${c.id}`} className="card block p-3 hover:opacity-90">
              <p className="doc-text text-sm font-medium" dir="auto">
                {c.title || "Untitled conversation"}
              </p>
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                {c.scope === "selected"
                  ? `${c.document_ids.length} document(s)`
                  : "whole library"}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
