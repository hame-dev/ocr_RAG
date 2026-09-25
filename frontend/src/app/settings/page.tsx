"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Check, CheckCircle2, Copy, Loader2, Monitor, Moon, RefreshCw, Sun, XCircle,
} from "lucide-react";
import { ApiError, api, changePassword, Engine, listEngines } from "@/lib/api";
import { fmt, num, StringKey } from "@/lib/i18n";
import { ACCENTS, ThemeMode } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useAuth } from "@/components/AuthProvider";
import { useLocale, useTheme } from "@/components/Providers";
import { Page, PageHeader } from "@/components/app-shell/Page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Hint } from "@/components/ui/tooltip";

const TIER: Record<Engine["tier"], StringKey> = {
  native: "tierNative", classical: "tierClassical", neural: "tierNeural", vlm: "tierVlm",
};

const CHECKS: Record<string, StringKey> = {
  database: "checkDatabase",
  redis: "checkRedis",
  ollama: "checkOllama",
  ocr_engines: "checkOcrEngines",
};

export default function SettingsPage() {
  const { t } = useLocale();
  return (
    <Page className="max-w-4xl">
      <PageHeader title={t("settings")} description={t("settingsSubtitle")} />
      <div className="space-y-6">
        <AppearanceCard />
        <PasswordCard />
        <SystemCard />
        <EnginesCard />
      </div>
    </Page>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-sm font-medium">{label}</span>
      <div>{children}</div>
    </div>
  );
}

function AppearanceCard() {
  const { t, locale, setLocale } = useLocale();
  const { mode, accent, setMode, setAccent } = useTheme();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("appearance")}</CardTitle>
        <CardDescription>{t("appearanceHint")}</CardDescription>
      </CardHeader>
      <CardContent className="divide-y">
        <Row label={t("theme")}>
          <Segmented<ThemeMode>
            value={mode}
            onChange={setMode}
            aria-label={t("theme")}
            options={[
              { value: "system", label: t("themeSystem"), icon: <Monitor /> },
              { value: "light", label: t("themeLight"), icon: <Sun /> },
              { value: "dark", label: t("themeDark"), icon: <Moon /> },
            ]}
          />
        </Row>
        <Row label={t("accentColor")}>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("accentColor")}>
            {ACCENTS.map((option) => {
              const active = accent === option.name;
              const label = locale === "ar" ? option.label_ar : option.label_en;
              return (
                <Hint key={option.name} label={label}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={active}
                    aria-label={label}
                    onClick={() => setAccent(option.name)}
                    className={cn(
                      "grid size-8 place-items-center rounded-full ring-offset-2 ring-offset-background transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active && "ring-2 ring-foreground/60",
                    )}
                    style={{ background: option.swatch }}
                  >
                    {active && <Check className="size-4 text-white" strokeWidth={3} />}
                  </button>
                </Hint>
              );
            })}
          </div>
        </Row>
        <Row label={t("language")}>
          <Segmented<"en" | "ar">
            value={locale}
            onChange={setLocale}
            aria-label={t("language")}
            options={[
              { value: "en", label: "English" },
              { value: "ar", label: "العربية" },
            ]}
          />
        </Row>
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const { t } = useLocale();
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const mismatch = confirm.length > 0 && next !== confirm;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (next !== confirm) return;
    setSaving(true);
    try {
      await changePassword(current, next);
      setCurrent(""); setNext(""); setConfirm("");
      toast.success(t("passwordChanged"));
    } catch (error) {
      // The backend's validator messages (too short, too common…) are shown as-is.
      toast.error(t("actionFailed"), { description: error instanceof ApiError ? error.detail : String(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("account")}</CardTitle>
        <CardDescription>
          {t("signedInAs")} <span className="font-medium text-foreground" dir="ltr">{user?.username}</span>
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid max-w-md gap-4">
          {/* Lets password managers attach the new password to the right account. */}
          <input type="text" autoComplete="username" value={user?.username ?? ""} hidden readOnly />
          <div className="space-y-2">
            <Label htmlFor="current-password">{t("currentPassword")}</Label>
            <Input id="current-password" type="password" autoComplete="current-password" dir="ltr"
                   required value={current} onChange={(event) => setCurrent(event.target.value)} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="new-password">{t("newPassword")}</Label>
              <Input id="new-password" type="password" autoComplete="new-password" dir="ltr"
                     required value={next} onChange={(event) => setNext(event.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">{t("confirmPassword")}</Label>
              <Input id="confirm-password" type="password" autoComplete="new-password" dir="ltr"
                     required value={confirm} aria-invalid={mismatch}
                     className={cn(mismatch && "border-destructive focus-visible:ring-destructive/30")}
                     onChange={(event) => setConfirm(event.target.value)} />
            </div>
          </div>
          {mismatch && <p className="text-xs text-destructive">{t("passwordsDontMatch")}</p>}
          <div>
            <Button type="submit" disabled={saving || !current || !next || !confirm || mismatch}>
              {saving && <Loader2 className="animate-spin" />}
              {t("changePassword")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function SystemCard() {
  const { t } = useLocale();
  const { data: health, isFetching, refetch } = useQuery({
    queryKey: ["health"],
    // A degraded system answers 503 with the same body; show it, don't throw.
    queryFn: () => api<any>("/api/health/deep/").catch((error) => error.body ?? null), // eslint-disable-line @typescript-eslint/no-explicit-any
    refetchInterval: 30_000,
  });
  const checks = Object.entries((health?.checks ?? {}) as Record<string, { ok: boolean; detail?: string }>);
  const allOk = health?.status === "ok";

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start gap-3 space-y-0">
        <div className="min-w-0 flex-1 basis-60">
          <CardTitle className="text-base">{t("systemStatus")}</CardTitle>
          <CardDescription className="mt-1.5">{t("systemStatusHint")}</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={cn(isFetching && "animate-spin")} /> {t("recheck")}
        </Button>
      </CardHeader>
      <CardContent>
        {!health ? (
          <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : (
          <>
            <div className={cn(
              "mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium",
              allOk ? "bg-success/10 text-success" : "bg-warning/10 text-warning",
            )}>
              {allOk ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
              {allOk ? t("allOperational") : t("someDegraded")}
            </div>
            <ul className="divide-y rounded-lg border">
              {checks.map(([name, check]) => (
                <li key={name} className="flex items-center gap-3 px-3 py-2.5">
                  <span className={cn("size-2 shrink-0 rounded-full", check.ok ? "bg-success" : "bg-destructive")} />
                  <span className="text-sm">{CHECKS[name] ? t(CHECKS[name]) : name}</span>
                  {check.detail && (
                    <span className="min-w-0 flex-1 truncate text-end text-xs text-muted-foreground" title={check.detail}>
                      {check.detail}
                    </span>
                  )}
                  <Badge variant={check.ok ? "success" : "destructive"} className={cn(!check.detail && "ms-auto")}>
                    {check.ok ? t("operational") : t("degraded")}
                  </Badge>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function EnginesCard() {
  const { t, locale } = useLocale();
  const { data, isPending } = useQuery({ queryKey: ["engines"], queryFn: () => listEngines() });
  const engines = [...(data?.engines ?? [])].sort((a, b) => Number(b.available) - Number(a.available));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("ocrEngines")}</CardTitle>
        <CardDescription>{t("ocrEnginesHint")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div>
        ) : (
          <ul className="divide-y rounded-lg border">
            {engines.map((engine) => (
              <li key={engine.name} className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn("text-sm font-medium", !engine.available && "text-muted-foreground")}>
                      {locale === "ar" ? engine.display_name_ar : engine.display_name_en}
                    </span>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{t(TIER[engine.tier])}</span>
                  </div>
                  {engine.available ? (
                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      {locale === "ar" ? engine.description_ar : engine.description_en}
                    </p>
                  ) : (
                    <FixCommand detail={engine.detail} />
                  )}
                </div>
                <div className="flex items-center gap-3 sm:justify-end">
                  <span className="tabular-nums text-xs text-muted-foreground">
                    {fmt(t("perPage"), { n: num(engine.est_seconds_per_page) })}
                  </span>
                  <Badge variant={engine.available ? "success" : "secondary"}>
                    {engine.available ? t("available") : t("unavailable")}
                  </Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** Probe detail, with the fix command (after "run:") copyable on its own. */
function FixCommand({ detail }: { detail: string }) {
  const { t } = useLocale();
  const [copied, setCopied] = useState(false);
  const command = detail.match(/run:\s*(.+)$/)?.[1]?.trim();
  return (
    <div className="mt-1 flex min-w-0 items-center gap-1.5">
      <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground" dir="ltr" title={detail}>
        {command ?? detail}
      </code>
      {command && (
        <Hint label={copied ? t("copied") : t("copyCommand")}>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-6"
            aria-label={t("copyCommand")}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(command);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              } catch {}
            }}
          >
            {copied ? <Check className="text-success" /> : <Copy />}
          </Button>
        </Hint>
      )}
    </div>
  );
}
