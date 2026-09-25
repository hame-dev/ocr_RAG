"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Eye, EyeOff, FileSearch, Languages, Loader2, MessagesSquare, ScanText } from "lucide-react";
import { ApiError } from "@/lib/api";
import { safeNext } from "@/lib/redirect";
import { useAuth } from "@/components/AuthProvider";
import { useLocale } from "@/components/Providers";
import { BrandMark } from "@/components/app-shell/AppSidebar";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function LoginForm() {
  const { t, locale, setLocale } = useLocale();
  const { status, login } = useAuth();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password);
      router.replace(next);
    } catch (err) {
      setPassword("");
      if (err instanceof ApiError && err.status === 400) setError(t("invalidCredentials"));
      else if (err instanceof ApiError && err.status === 429) setError(t("tooManyAttempts"));
      else setError(t("signInFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  const features = [
    { icon: ScanText, text: t("stepExtractHint") },
    { icon: FileSearch, text: t("stepReviewHint") },
    { icon: MessagesSquare, text: t("stepChatHint") },
  ];

  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-2">
      {/* Brand panel: desktop only. */}
      <aside className="relative hidden overflow-hidden border-e bg-sidebar lg:flex lg:flex-col lg:justify-between lg:p-10">
        <div
          className="pointer-events-none absolute -top-40 start-[-10rem] size-[32rem] rounded-full bg-primary/15 blur-3xl"
          aria-hidden
        />
        <div className="relative flex items-center gap-2.5">
          <BrandMark />
          <span className="font-semibold tracking-tight">{t("appName")}</span>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">{t("tagline")}</h2>
          <ul className="mt-8 space-y-4">
            {features.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-3 text-sm text-muted-foreground">
                <span className="grid size-8 shrink-0 place-items-center rounded-lg border bg-background text-foreground">
                  <Icon className="size-4" />
                </span>
                <span className="pt-1.5">{text}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-muted-foreground">{t("signInHint")}</p>
      </aside>

      <main className="flex flex-col px-4 py-6 sm:px-8">
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" onClick={() => setLocale(locale === "ar" ? "en" : "ar")}>
            <Languages />
            {locale === "ar" ? "English" : "العربية"}
          </Button>
        </div>

        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <BrandMark className="mb-6 size-10 lg:hidden" />
          <h1 className="text-2xl font-semibold tracking-tight">{t("welcomeBack")}</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{t("signInSubtitle")}</p>

          <form onSubmit={submit} className="mt-8 space-y-5" noValidate>
            {error && (
              <Alert variant="destructive">
                <AlertCircle />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <div className="space-y-2">
              <Label htmlFor="username">{t("username")}</Label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                dir="ltr"
                required
                autoFocus
                className="h-10"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">{t("password")}</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  name="password"
                  autoComplete="current-password"
                  dir="ltr"
                  required
                  className="h-10 pe-10"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="absolute inset-y-0 end-0 grid w-10 place-items-center text-muted-foreground hover:text-foreground"
                  aria-label={showPassword ? t("hidePassword") : t("showPassword")}
                  aria-pressed={showPassword}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            <Button
              type="submit"
              size="lg"
              className="w-full"
              disabled={submitting || !username.trim() || !password}
            >
              {submitting && <Loader2 className="animate-spin" />}
              {submitting ? t("signingIn") : t("signIn")}
            </Button>
          </form>

          <p className="mt-8 text-center text-xs text-muted-foreground lg:hidden">{t("signInHint")}</p>
        </div>
      </main>
    </div>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary for static rendering.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
