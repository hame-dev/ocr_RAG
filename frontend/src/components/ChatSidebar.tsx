"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, ConversationSummary } from "@/lib/api";
import { useLocale } from "./Providers";

interface ChatSidebarProps {
  activeId?: string;
  collapsed: boolean;
  mobileOpen: boolean;
  onCollapse: () => void;
  onMobileClose: () => void;
}

export function ChatSidebar({
  activeId,
  collapsed,
  mobileOpen,
  onCollapse,
  onMobileClose,
}: ChatSidebarProps) {
  const { t, locale, setLocale } = useLocale();
  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<{ results: ConversationSummary[] }>("/api/conversations/"),
  });

  const conversations = data?.results ?? [];

  const content = (mobile: boolean) => (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-3 py-3">
        <Link href="/" className="min-w-0 flex-1 truncate text-sm font-semibold">
          {t("appName")}
        </Link>
        <button
          type="button"
          onClick={mobile ? onMobileClose : onCollapse}
          className="grid h-8 w-8 place-items-center rounded-lg text-lg hover:bg-[var(--hover)]"
          aria-label={mobile ? t("close") : t("hideSidebar")}
          title={mobile ? t("close") : t("hideSidebar")}
        >
          {mobile ? "×" : <span className="flip-rtl">‹</span>}
        </button>
      </div>

      <div className="px-3 pb-2">
        <Link
          href="/chat"
          onClick={onMobileClose}
          className="flex items-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-medium hover:bg-[var(--hover)]"
          style={{ borderColor: "var(--border)" }}
        >
          <span className="text-lg leading-none">＋</span>
          {t("newChat")}
        </Link>
      </div>

      <nav className="space-y-0.5 px-3 py-2 text-sm" aria-label={t("appNavigation")}>
        <Link href="/" onClick={onMobileClose} className="chat-nav-link">
          <span aria-hidden>▦</span><span>{t("library")}</span>
        </Link>
        <Link href="/upload" onClick={onMobileClose} className="chat-nav-link">
          <span aria-hidden>↑</span><span>{t("upload")}</span>
        </Link>
        <Link href="/chat" onClick={onMobileClose} className="chat-nav-link chat-nav-active">
          <span aria-hidden>◌</span><span>{t("chat")}</span>
        </Link>
      </nav>

      <div className="mt-2 flex min-h-0 flex-1 flex-col border-t px-3 pt-3"
           style={{ borderColor: "var(--border)" }}>
        <p className="px-2 pb-2 text-xs font-medium" style={{ color: "var(--muted)" }}>
          {t("recentChats")}
        </p>
        <nav className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">
          {conversations.length === 0 ? (
            <p className="px-2 py-3 text-xs" style={{ color: "var(--muted)" }}>
              {t("noConversations")}
            </p>
          ) : (
            <ul className="space-y-0.5 pb-3">
              {conversations.map((conversation) => {
                const active = conversation.id === activeId;
                return (
                  <li key={conversation.id}>
                    <Link
                      href={`/chat/${conversation.id}`}
                      onClick={onMobileClose}
                      aria-current={active ? "page" : undefined}
                      className="block rounded-xl px-2.5 py-2 transition-colors hover:bg-[var(--hover)]"
                      style={{
                        background: active ? "var(--hover)" : undefined,
                        borderInlineStart: active ? "2px solid var(--accent)" : "2px solid transparent",
                      }}
                    >
                      <span className="doc-text line-clamp-1 text-sm" dir="auto">
                        {conversation.title || t("untitled")}
                      </span>
                      <span className="mt-0.5 block text-[11px]" style={{ color: "var(--muted)" }}>
                        {conversation.scope === "selected"
                          ? t("selectedSourceCount").replace("{count}", String(conversation.document_ids.length))
                          : t("wholeLibrary")}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>
      </div>

      <div className="space-y-1 border-t p-3 text-sm" style={{ borderColor: "var(--border)" }}>
        <Link href="/settings" onClick={onMobileClose} className="chat-nav-link">
          <span aria-hidden>⚙</span><span>{t("settings")}</span>
        </Link>
        <button
          type="button"
          onClick={() => setLocale(locale === "ar" ? "en" : "ar")}
          className="chat-nav-link w-full"
        >
          <span aria-hidden>文</span>
          <span>{locale === "ar" ? "English" : "العربية"}</span>
        </button>
      </div>
    </div>
  );

  return (
    <>
      {!collapsed && (
        <aside
          className="hidden h-dvh w-72 shrink-0 border-e lg:block"
          style={{ background: "var(--sidebar)", borderColor: "var(--border)" }}
        >
          {content(false)}
        </aside>
      )}

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/45"
            onClick={onMobileClose}
            aria-label={t("close")}
          />
          <aside
            className="absolute inset-y-0 start-0 w-72 max-w-[88vw] border-e shadow-2xl"
            style={{ background: "var(--sidebar)", borderColor: "var(--border)" }}
          >
            {content(true)}
          </aside>
        </div>
      )}
    </>
  );
}
