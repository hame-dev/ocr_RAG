"use client";

import { useQuery } from "@tanstack/react-query";
import { api, listEngines } from "@/lib/api";
import { num } from "@/lib/i18n";
import { useLocale } from "@/components/Providers";

/**
 * Operator view: which engines are healthy, and why the unhealthy ones are not.
 * Each unavailable engine shows the health probe's own explanation, so a missing
 * `ollama pull` is self-diagnosing.
 */
export default function SettingsPage() {
  const { locale, t } = useLocale();
  const { data: engineData, refetch } = useQuery({
    queryKey: ["engines", "settings"],
    queryFn: () => listEngines(true),
  });
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api<any>("/api/health/deep/").catch((e) => e.body ?? null),
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center">
        <h1 className="text-xl font-semibold">{t("settings")}</h1>
        <button
          onClick={() => refetch()}
          className="ms-auto rounded-md border px-3 py-1.5 text-sm"
          style={{ borderColor: "var(--border)" }}
        >
          Re-probe
        </button>
      </div>

      {health && (
        <section className="card p-4">
          <h2 className="mb-2 font-medium">System</h2>
          <ul className="space-y-1 text-sm">
            {Object.entries(health.checks ?? {}).map(([name, check]: [string, any]) => (
              <li key={name} className="flex items-start gap-2">
                <span style={{ color: check.ok ? "#22c55e" : "#ef4444" }}>
                  {check.ok ? "●" : "○"}
                </span>
                <span className="font-medium">{name}</span>
                <span className="ms-auto text-xs" style={{ color: "var(--muted)" }}>
                  {check.detail || check.base_url || (check.available ?? []).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card p-4">
        <h2 className="mb-3 font-medium">OCR engines</h2>
        <ul className="space-y-2">
          {(engineData?.engines ?? []).map((e) => (
            <li key={e.name} className="flex items-start gap-3 border-b pb-2 last:border-0"
                style={{ borderColor: "var(--border)" }}>
              <span style={{ color: e.available ? "#22c55e" : "var(--muted)" }}>
                {e.available ? "●" : "○"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">
                  {locale === "ar" ? e.display_name_ar : e.display_name_en}
                  <span className="ms-2 text-xs" style={{ color: "var(--muted)" }}>
                    {e.tier}
                  </span>
                </div>
                <p className="text-xs" style={{ color: "var(--muted)" }}>
                  {e.available
                    ? (locale === "ar" ? e.description_ar : e.description_en)
                    : e.detail}
                </p>
              </div>
              <span className="numeric text-xs" style={{ color: "var(--muted)" }}>
                ~{num(e.est_seconds_per_page)}s/pg
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
