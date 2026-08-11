"use client";

import { useEffect, useId, useState } from "react";
import { useLocale, useTheme } from "./Providers";

// Mermaid uses shared global configuration and a temporary DOM node while it
// renders. Serialising jobs prevents two diagrams from changing the theme or
// removing each other's temporary node during the same React commit.
let renderQueue: Promise<void> = Promise.resolve();

function enqueue<T>(task: () => Promise<T>): Promise<T> {
  const result = renderQueue.then(task, task);
  renderQueue = result.then(() => undefined, () => undefined);
  return result;
}

export function MermaidDiagram({ source }: { source: string }) {
  const { t } = useLocale();
  const { mode } = useTheme();
  const reactId = useId();
  const [svg, setSvg] = useState("");
  const [error, setError] = useState(false);
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg("");
    setError(false);

    // During streaming, wait for a short pause so an unfinished Mermaid block
    // is not parsed on every token.
    const timer = window.setTimeout(() => {
      void enqueue(async () => {
        if (source.length > 12_000) throw new Error("diagram source is too large");
        const mermaid = (await import("mermaid")).default;
        const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        const dark = mode === "dark" || (mode === "system" && systemDark);
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          suppressErrorRendering: true,
          maxTextSize: 12_000,
          theme: dark ? "dark" : "neutral",
          flowchart: { htmlLabels: false, useMaxWidth: true },
        });
        const renderId = `mermaid-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
        return mermaid.render(renderId, source);
      })
        .then((result) => {
          if (!cancelled) setSvg(result.svg);
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [mode, reactId, source]);

  return (
    <figure className="mermaid-diagram" aria-label={t("diagram")}>
      <div className="mb-2 flex items-center gap-2 text-xs" style={{ color: "var(--muted)" }}>
        <span>{t("diagram")}</span>
        <button
          type="button"
          onClick={() => setShowSource((value) => !value)}
          className="ms-auto rounded-lg px-2 py-1 hover:bg-[var(--hover)]"
        >
          {showSource ? t("hideDiagramSource") : t("showDiagramSource")}
        </button>
      </div>

      {showSource ? (
        <pre><code>{source}</code></pre>
      ) : svg ? (
        // Mermaid's strict security mode sanitizes generated SVG and disables
        // model-authored HTML labels and interactive links.
        <div className="overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : error ? (
        <div>
          <p className="text-xs" style={{ color: "var(--muted)" }}>
            {t("diagramUnavailable")}
          </p>
          <pre><code>{source}</code></pre>
        </div>
      ) : (
        <div className="flex items-center gap-2 py-5 text-xs" style={{ color: "var(--muted)" }}>
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          {t("renderingDiagram")}
        </div>
      )}
    </figure>
  );
}
