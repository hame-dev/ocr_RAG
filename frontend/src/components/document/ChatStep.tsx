"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Loader2, MessageSquare, MessagesSquare, Clock } from "lucide-react";
import { useState } from "react";
import { listConversations } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "../app-shell/Page";
import { useLocale } from "../Providers";

export function ChatStep({
  documentId, ready, onStart,
}: {
  documentId: string;
  ready: boolean;
  onStart: () => Promise<void>;
}) {
  const { t, locale } = useLocale();
  const [starting, setStarting] = useState(false);
  const { data } = useQuery({ queryKey: ["conversations"], queryFn: listConversations, enabled: ready });
  const related = (data?.results ?? []).filter((c) => c.document_ids.includes(documentId));

  if (!ready) {
    return <EmptyState icon={<Clock />} title={t("stepChat")} description={t("chatNotReady")} />;
  }

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <CardContent className="flex flex-col items-start gap-4 p-6 sm:flex-row sm:items-center">
          <span className="grid size-12 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
            <MessagesSquare className="size-6" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold">{t("chatReadyTitle")}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{t("chatReadyHint")}</p>
          </div>
          <Button
            size="lg"
            disabled={starting}
            onClick={async () => {
              setStarting(true);
              try { await onStart(); } finally { setStarting(false); }
            }}
          >
            {starting ? <Loader2 className="animate-spin" /> : <MessagesSquare />}
            {t("startChat")}
          </Button>
        </CardContent>
      </Card>

      {related.length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-medium text-muted-foreground">{t("recentChats")}</h3>
          <ul className="divide-y rounded-xl border">
            {related.map((conversation) => (
              <li key={conversation.id}>
                <Link
                  href={`/chat/${conversation.id}`}
                  className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
                >
                  <MessageSquare className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-sm" dir="auto">
                    {conversation.title || t("untitled")}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {relativeTime(conversation.updated_at, locale)}
                  </span>
                  <ArrowRight className="size-4 shrink-0 text-muted-foreground rtl:-scale-x-100" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
