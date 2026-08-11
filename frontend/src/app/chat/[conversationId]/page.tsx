"use client";

import { useParams } from "next/navigation";
import { ChatWorkspace } from "@/components/ChatWorkspace";

export default function ConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  return <ChatWorkspace conversationId={conversationId} />;
}
