"use client";

import { Engine } from "@/lib/api";
import { num } from "@/lib/i18n";
import { useLocale } from "./Providers";

const TIER_LABEL: Record<string, string> = {
  native: "instant",
  classical: "fast",
  neural: "accurate",
  vlm: "AI vision",
};

/**
 * Engine picker. Only engines the health probe reports as available are
 * selectable; the rest are greyed with the probe's own explanation, so a missing
 * model or a down sidecar is self-explaining rather than a mystery.
 */
export function EngineSelector({
  engines, selected, onChange, pageCount, isDigital, onRun, busy,
}: {
  engines: Engine[];
  selected: string[];
  onChange: (next: string[]) => void;
  pageCount: number;
  isDigital: boolean;
  onRun: () => void;
  busy: boolean;
}) {
  const { t, locale } = useLocale();

  // native_pdf only makes sense when there is a text layer to read.
  const visible = engines.filter((e) => e.name !== "native_pdf" || isDigital);

  const toggle = (name: string) =>
    onChange(selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name]);

  const estimate = selected.reduce((total, name) => {
    const engine = engines.find((e) => e.name === name);
    return total + (engine ? engine.est_seconds_per_page * pageCount : 0);
  }, 0);

  return (
    <section className="card p-4">
      <div className="mb-1 flex items-center gap-3">
        <h2 className="font-medium">{t("selectEngines")}</h2>
        {selected.length > 0 && (
          <span className="numeric text-xs" style={{ color: "var(--muted)" }}>
            ~{num(Math.round(estimate))}s
          </span>
        )}
        <button
          onClick={onRun}
          disabled={!selected.length || busy}
          className="ms-auto rounded-md px-3 py-1.5 text-sm text-white disabled:opacity-40"
          style={{ background: "var(--accent)" }}
        >
          {t("runOcr")}
        </button>
      </div>
      <p className="mb-3 text-xs" style={{ color: "var(--muted)" }}>{t("engineHint")}</p>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {visible.map((engine) => {
          const isSelected = selected.includes(engine.name);
          const name = locale === "ar" ? engine.display_name_ar : engine.display_name_en;
          const description = locale === "ar" ? engine.description_ar : engine.description_en;

          return (
            <button
              key={engine.name}
              onClick={() => engine.available && toggle(engine.name)}
              disabled={!engine.available}
              title={engine.available ? description : engine.detail}
              className="rounded-lg border p-3 text-start transition-colors disabled:cursor-not-allowed disabled:opacity-45"
              style={{
                borderColor: isSelected ? "var(--accent)" : "var(--border)",
                background: isSelected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : undefined,
              }}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{name}</span>
                <span
                  className="ms-auto rounded px-1.5 py-0.5 text-[10px]"
                  style={{ background: "var(--border)", color: "var(--muted)" }}
                >
                  {TIER_LABEL[engine.tier] ?? engine.tier}
                </span>
              </div>

              <p className="mt-1 line-clamp-2 text-xs" style={{ color: "var(--muted)" }}>
                {engine.available ? description : engine.detail}
              </p>

              <div className="mt-1.5 flex gap-2 text-[10px]" style={{ color: "var(--muted)" }}>
                <span className="numeric">~{num(engine.est_seconds_per_page)}s/{t("page")}</span>
                {engine.supports_boxes && <span>boxes</span>}
                {engine.supports_confidence && <span>confidence</span>}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
