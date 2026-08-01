"use client";

import { useLocale } from "./Providers";

/**
 * The standardized metadata record, plus the LLM's field proposal.
 *
 * Core fields render in a fixed order for EVERY document — that consistency is
 * the whole point of the standard schema, and seeing it makes the guarantee
 * legible to the user.
 */
export function MetadataPanel({ metadata, plan }: { metadata: any; plan: any }) {
  const { t } = useLocale();
  if (!metadata && !plan?.proposed_fields?.length) return null;

  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="font-medium">{t("metadata")}</h2>
        {metadata?.is_partial && (
          <span className="rounded px-2 py-0.5 text-xs" style={{ background: "#fef3c7", color: "#92400e" }}>
            partial
          </span>
        )}
        {metadata?.human_edited && (
          <span className="rounded px-2 py-0.5 text-xs" style={{ background: "#dbeafe", color: "#1e40af" }}>
            edited by you
          </span>
        )}
      </div>

      {plan?.proposed_fields?.length > 0 && !metadata && (
        <div className="mb-4">
          <p className="mb-2 text-sm" style={{ color: "var(--muted)" }}>
            The model found these extractable fields:
          </p>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {plan.proposed_fields.map((f: any) => (
              <li key={f.key} className="rounded border p-2 text-xs" style={{ borderColor: "var(--border)" }}>
                <div className="font-medium">{f.label_en}</div>
                <div className="doc-text" dir="auto" style={{ color: "var(--muted)" }}>
                  {f.example_value}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {metadata && (
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <Field label="Type" value={metadata.doc_type} />
          <Field label="Title" value={metadata.title} rtlAware />
          <Field label="Title (Latin)" value={metadata.title_translit} />
          <Field label="Language" value={`${metadata.primary_language} (${(metadata.languages ?? []).join(", ")})`} />
          <Field label="Date" value={metadata.document_date} />
          <Field label="Reference" value={(metadata.identifiers?.ref_numbers ?? []).join(", ")} />
          <Field label="Amounts" value={(metadata.identifiers?.amounts ?? []).join(", ")} rtlAware />
          <Field label="People" value={(metadata.entities?.persons ?? []).join(", ")} rtlAware />
          <Field label="Organizations" value={(metadata.entities?.organizations ?? []).join(", ")} rtlAware />
          <Field label="Keywords" value={(metadata.keywords ?? []).join(" · ")} rtlAware />

          <div className="sm:col-span-2">
            <dt className="text-xs" style={{ color: "var(--muted)" }}>Summary</dt>
            <dd className="doc-text text-sm" dir="auto">{metadata.summary_short}</dd>
          </div>

          {metadata.custom_fields && Object.keys(metadata.custom_fields).length > 0 && (
            <div className="sm:col-span-2 mt-2 border-t pt-2" style={{ borderColor: "var(--border)" }}>
              <dt className="mb-1 text-xs" style={{ color: "var(--muted)" }}>
                Document-specific fields
              </dt>
              <dd className="grid gap-1 sm:grid-cols-2">
                {Object.entries(metadata.custom_fields).map(([key, value]) => (
                  <div key={key} className="text-sm">
                    <span className="text-xs" style={{ color: "var(--muted)" }}>{key}: </span>
                    <span className="doc-text" dir="auto">{String(value ?? "—")}</span>
                  </div>
                ))}
              </dd>
            </div>
          )}

          {metadata.quality_flags?.length > 0 && (
            <div className="sm:col-span-2">
              <dt className="text-xs" style={{ color: "var(--muted)" }}>Quality flags</dt>
              <dd className="flex flex-wrap gap-1">
                {metadata.quality_flags.map((flag: string) => (
                  <span key={flag} className="rounded px-1.5 py-0.5 text-xs"
                        style={{ background: "#fef3c7", color: "#92400e" }}>
                    {flag}
                  </span>
                ))}
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}

function Field({ label, value, rtlAware }: { label: string; value: any; rtlAware?: boolean }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs" style={{ color: "var(--muted)" }}>{label}</dt>
      <dd className={rtlAware ? "doc-text text-sm" : "text-sm"} dir={rtlAware ? "auto" : undefined}>
        {String(value)}
      </dd>
    </div>
  );
}
