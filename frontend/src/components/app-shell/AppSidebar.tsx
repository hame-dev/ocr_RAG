"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronsUpDown, Ellipsis, FolderOpen, Languages, LogOut, MessagesSquare, Monitor, Moon,
  PanelLeftClose, PanelLeftOpen, Settings, SquarePen, Sun, Trash2, Upload,
} from "lucide-react";
import { toast } from "sonner";
import { api, ConversationSummary, deleteConversation } from "@/lib/api";
import { fmt, StringKey } from "@/lib/i18n";
import { ThemeMode } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Hint } from "@/components/ui/tooltip";
import { useAuth } from "../AuthProvider";
import { useLocale, useTheme } from "../Providers";

const NAV: { href: string; label: StringKey; icon: typeof FolderOpen; match: (p: string) => boolean }[] = [
  { href: "/", label: "library", icon: FolderOpen, match: (p) => p === "/" || p.startsWith("/documents") },
  { href: "/upload", label: "upload", icon: Upload, match: (p) => p.startsWith("/upload") },
  { href: "/chat", label: "chat", icon: MessagesSquare, match: (p) => p.startsWith("/chat") },
  { href: "/settings", label: "settings", icon: Settings, match: (p) => p.startsWith("/settings") },
];

/**
 * The logo mark. It sits on a white tile in both themes: the navy stroke of
 * the logo would disappear against the dark background otherwise.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "grid size-8 shrink-0 place-items-center overflow-hidden rounded-lg bg-white p-[3px] shadow-sm ring-1 ring-black/5",
        className,
      )}
      aria-hidden
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- tiny static asset */}
      <img src="/brand/logo-mark.png" alt="" className="size-full object-contain" draggable={false} />
    </span>
  );
}

interface AppSidebarProps {
  /** Icon-only rail (desktop). Ignored in the mobile sheet. */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  /** Called after any navigation, so the mobile sheet can close itself. */
  onNavigate?: () => void;
}

export function AppSidebar({ collapsed = false, onToggleCollapsed, onNavigate }: AppSidebarProps) {
  const { t } = useLocale();
  const pathname = usePathname();
  const activeChat = pathname.startsWith("/chat/") ? pathname.split("/")[2] : null;
  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<{ results: ConversationSummary[] }>("/api/conversations/"),
  });
  const conversations = data?.results ?? [];
  const [pendingDelete, setPendingDelete] = useState<ConversationSummary | null>(null);

  const item = (href: string, label: string, Icon: typeof FolderOpen, active: boolean) => {
    const link = (
      <Link
        href={href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex h-9 items-center gap-3 rounded-lg px-2.5 text-sm transition-colors",
          active
            ? "bg-accent font-medium text-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon className="size-[18px] shrink-0" />
        {!collapsed && <span className="truncate">{label}</span>}
      </Link>
    );
    return collapsed ? <Hint label={label} side="right">{link}</Hint> : link;
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={cn("flex h-14 items-center gap-2.5 px-3", collapsed && "justify-center px-0")}>
        {!collapsed && (
          <Link href="/" onClick={onNavigate} className="flex min-w-0 flex-1 items-center gap-2.5">
            <BrandMark />
            <span className="truncate text-sm font-semibold tracking-tight">{t("appName")}</span>
          </Link>
        )}
        {onToggleCollapsed && (
          <Hint label={t("toggleSidebar")} side="right">
            <Button variant="ghost" size="icon-sm" onClick={onToggleCollapsed} aria-label={t("toggleSidebar")}>
              {collapsed ? <PanelLeftOpen className="rtl:-scale-x-100" /> : <PanelLeftClose className="rtl:-scale-x-100" />}
            </Button>
          </Hint>
        )}
      </div>

      <div className={cn("px-3 pb-2", collapsed && "px-2")}>
        {collapsed ? (
          <Hint label={t("newChat")} side="right">
            <Button asChild variant="outline" size="icon" className="w-full">
              <Link href="/chat" onClick={onNavigate} aria-label={t("newChat")}>
                <SquarePen />
              </Link>
            </Button>
          </Hint>
        ) : (
          <Button asChild variant="outline" className="w-full justify-start gap-2.5 bg-background">
            <Link href="/chat" onClick={onNavigate}>
              <SquarePen />
              {t("newChat")}
            </Link>
          </Button>
        )}
      </div>

      <nav className={cn("space-y-0.5 px-3 py-2", collapsed && "px-2")} aria-label={t("appNavigation")}>
        {NAV.map((entry) => (
          <Fragment key={entry.href}>
            {item(entry.href, t(entry.label), entry.icon, entry.match(pathname) && !activeChat)}
          </Fragment>
        ))}
      </nav>

      {!collapsed ? (
        <div className="mt-2 flex min-h-0 flex-1 flex-col">
          <p className="px-5 pb-1.5 pt-2 text-xs font-medium text-muted-foreground">{t("recentChats")}</p>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {conversations.length === 0 ? (
              <p className="px-2.5 py-2 text-xs text-muted-foreground">{t("noConversations")}</p>
            ) : (
              <ul className="space-y-0.5">
                {conversations.slice(0, 30).map((conversation) => (
                  <RecentChat
                    key={conversation.id}
                    conversation={conversation}
                    active={conversation.id === activeChat}
                    onNavigate={onNavigate}
                    onDelete={setPendingDelete}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : (
        <div className="flex-1" />
      )}

      <div className={cn("border-t p-2", collapsed && "flex justify-center")}>
        <UserMenu collapsed={collapsed} />
      </div>

      <DeleteChatDialog
        conversation={pendingDelete}
        activeChat={activeChat}
        onClose={() => setPendingDelete(null)}
      />
    </div>
  );
}

/** A recent-chat row. Its menu opens from the ⋯ button or a right-click. */
function RecentChat({
  conversation, active, onNavigate, onDelete,
}: {
  conversation: ConversationSummary;
  active: boolean;
  onNavigate?: () => void;
  onDelete: (conversation: ConversationSummary) => void;
}) {
  const { t } = useLocale();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li className="group/chat relative">
      <Link
        href={`/chat/${conversation.id}`}
        onClick={onNavigate}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen(true);
        }}
        aria-current={active ? "page" : undefined}
        className={cn(
          "block truncate rounded-lg py-1.5 pe-8 ps-2.5 text-sm transition-colors",
          active || menuOpen
            ? "bg-accent font-medium text-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        )}
        dir="auto"
      >
        {conversation.title || t("untitled")}
      </Link>
      {/* Not modal: its item opens a dialog, and a modal menu closing
          underneath a dialog opening can leave the page unclickable. */}
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t("chatOptions")}
            className={cn(
              "absolute end-1 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded-md text-muted-foreground transition-opacity",
              "hover:bg-background/60 hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              // Always visible on touch screens, which have no hover.
              "opacity-0 group-hover/chat:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100",
            )}
          >
            <Ellipsis className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem destructive onSelect={() => onDelete(conversation)}>
            <Trash2 /> {t("deleteChat")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

function DeleteChatDialog({
  conversation, activeChat, onClose,
}: {
  conversation: ConversationSummary | null;
  activeChat: string | null;
  onClose: () => void;
}) {
  const { t } = useLocale();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);

  async function confirm() {
    if (!conversation) return;
    const id = conversation.id;
    setDeleting(true);
    try {
      await deleteConversation(id);
      queryClient.setQueryData<{ results: ConversationSummary[] }>(["conversations"], (old) =>
        old && { ...old, results: old.results.filter((c) => c.id !== id) },
      );
      queryClient.removeQueries({ queryKey: ["conversation", id] });
      // Leave the chat before it disappears from under the open page.
      if (id === activeChat) router.push("/chat");
      toast.success(t("chatDeleted"));
      onClose();
    } catch (error) {
      toast.error(t("deleteChatFailed"), { description: error instanceof Error ? error.message : undefined });
    } finally {
      setDeleting(false);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }

  return (
    <Dialog open={conversation !== null} onOpenChange={(open) => !open && !deleting && onClose()}>
      <DialogContent className="max-w-md" closeLabel={t("close")}>
        <DialogHeader>
          <DialogTitle>{t("deleteChatTitle")}</DialogTitle>
          <DialogDescription dir="auto">
            {fmt(t("deleteChatBody"), { title: conversation?.title || t("untitled") })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={deleting}>{t("cancel")}</Button>
          <Button variant="destructive" onClick={() => void confirm()} disabled={deleting}>
            <Trash2 /> {t("deleteAction")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function UserMenu({ collapsed }: { collapsed: boolean }) {
  const { t, locale, setLocale } = useLocale();
  const { mode, setMode } = useTheme();
  const { user, logout } = useAuth();
  const initial = (user?.username ?? "?").charAt(0).toUpperCase();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t("accountMenu")}
          className={cn(
            "flex w-full items-center gap-2.5 rounded-lg p-1.5 text-start transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            collapsed && "w-auto",
          )}
        >
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
            {initial}
          </span>
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium" dir="ltr">{user?.username}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {t("account")}
                </span>
              </span>
              <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
            </>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuLabel>
          {t("signedInAs")} <span className="text-foreground" dir="ltr">{user?.username}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="flex items-center gap-2">
          <Languages className="size-3.5" /> {t("language")}
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup value={locale} onValueChange={(v) => setLocale(v as "en" | "ar")}>
          <DropdownMenuRadioItem value="en">English</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="ar">العربية</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>{t("theme")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={mode} onValueChange={(v) => setMode(v as ThemeMode)}>
          <DropdownMenuRadioItem value="system"><Monitor /> {t("themeSystem")}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light"><Sun /> {t("themeLight")}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark"><Moon /> {t("themeDark")}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings"><Settings /> {t("settings")}</Link>
        </DropdownMenuItem>
        <DropdownMenuItem destructive onSelect={() => void logout()}>
          <LogOut className="rtl:-scale-x-100" /> {t("signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
