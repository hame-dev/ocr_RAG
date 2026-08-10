"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { api, createConversation } from "@/lib/api";
import { useLocale } from "@/components/Providers";
import { ChatSidebar } from "@/components/ChatSidebar";

export default function ChatIndexPage() {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<any>("/api/conversations/"),
  });

  async function startNew() {
    if (creating) return;
    setCreating(true);
    try {
      const conversation = await createConversation("all");
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      router.push(`/chat/${conversation.id}`);
    } finally {
      setCreating(false);
    }
  }

  const conversations = data?.results ?? [];

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      <ChatSidebar />

      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mb-5 flex items-center">
          <h1 className="text-xl font-semibold">{t("chat")}</h1>
          <button
            onClick={startNew}
            disabled={creating}
            className="ms-auto rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-50"
            style={{ background: "var(--accent)" }}
          >
            + {t("newChat")}
          </button>
        </div>

        {conversations.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--muted)" }}>
            {t("noConversations")}
          </p>
        ) : (
          <ul className="space-y-2">
            {conversations.map((c: any) => (
              <li key={c.id}>
                <Link href={`/chat/${c.id}`} className="card block p-3 hover:opacity-90">
                  <p className="doc-text text-sm font-medium" dir="auto">
                    {c.title || t("untitled")}
                  </p>
                  <p className="text-xs" style={{ color: "var(--muted)" }}>
                    {c.scope === "selected"
                      ? `${c.document_ids.length} document(s)`
                      : t("wholeLibrary")}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
