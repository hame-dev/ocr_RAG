"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, createConversation } from "@/lib/api";
import { useLocale } from "./Providers";

interface Conversation {
  id: string;
  title: string | null;
  scope: string;
  document_ids: string[];
  updated_at?: string;
  created_at?: string;
}

/**
 * Conversation history rail, in the ChatGPT/Claude idiom: a persistent list of
 * past chats on the inline-start edge, with the active one highlighted.
 *
 * It collapses to a drawer under `lg` so the chat column keeps its full width
 * on a phone. `start-0` / `ps-*` are logical, so the rail sits on the right in
 * Arabic without a second set of rules.
 */
export function ChatSidebar({ activeId }: { activeId?: string }) {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<{ results: Conversation[] }>("/api/conversations/"),
  });

  const conversations = data?.results ?? [];

  async function startNew() {
    if (creating) return;
    setCreating(true);
    try {
      const conversation = await createConversation("all");
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setOpen(false);
      router.push(`/chat/${conversation.id}`);
    } finally {
      setCreating(false);
    }
  }

  const list = (
    <div className="flex h-full flex-col gap-3">
      <button
        onClick={startNew}
        disabled={creating}
        className="rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        style={{ background: "var(--accent)" }}
      >
        + {t("newChat")}
      </button>

      <p className="px-1 text-xs font-medium uppercase tracking-wide"
         style={{ color: "var(--muted)" }}>
        {t("history")}
      </p>

      <nav className="-mx-1 flex-1 overflow-y-auto px-1">
        {conversations.length === 0 && (
          <p className="px-2 py-3 text-xs" style={{ color: "var(--muted)" }}>
            {t("noConversations")}
          </p>
        )}

        <ul className="space-y-0.5">
          {conversations.map((c) => {
            const active = c.id === activeId;
            return (
              <li key={c.id}>
                <Link
                  href={`/chat/${c.id}`}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className="block rounded-lg px-2.5 py-2 transition-colors"
                  style={{
                    background: active ? "var(--hover)" : undefined,
                    // The active row gets a hairline in the accent so it reads
                    // as selected even when --hover is a very low-contrast fill.
                    boxShadow: active ? "inset 2px 0 0 0 var(--accent)" : undefined,
                  }}
                >
                  <span className="doc-text line-clamp-2 text-sm" dir="auto">
                    {c.title || t("untitled")}
                  </span>
                  <span className="mt-0.5 block text-xs" style={{ color: "var(--muted)" }}>
                    {c.scope === "selected"
                      ? `${c.document_ids.length} doc${c.document_ids.length === 1 ? "" : "s"}`
                      : t("wholeLibrary")}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );

  return (
    <>
      {/* Mobile trigger */}
      <button
        onClick={() => setOpen(true)}
        className="mb-3 rounded-md border px-3 py-1.5 text-sm lg:hidden"
        style={{ borderColor: "var(--border)" }}
        aria-expanded={open}
      >
        ☰ {t("history")}
      </button>

      {/* Desktop rail */}
      <aside
        className="hidden w-64 shrink-0 border-e pe-3 lg:block"
        style={{ borderColor: "var(--border)" }}
      >
        {list}
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-30 lg:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            className="absolute inset-y-0 start-0 w-72 max-w-[85vw] p-4 shadow-xl"
            style={{ background: "var(--bg)" }}
          >
            {list}
          </div>
        </div>
      )}
    </>
  );
}
