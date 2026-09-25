"use client";

import { Children, isValidElement, memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { MermaidDiagram } from "./MermaidDiagram";

interface Props {
  children: string;
  /** Bubbles filled with --accent need their own code/table surfaces. */
  onAccent?: boolean;
}

/**
 * Chat answers are markdown. Rendering them as plain text leaks `**bold**` and
 * `1.` list markers into the UI, which is what this fixes.
 *
 * No raw-HTML plugin is enabled: model output is untrusted, and react-markdown
 * escapes HTML by default. Keep it that way.
 *
 * Memoised on the source string because a streaming message re-renders on every
 * token, and re-parsing an unchanged sibling message is pure waste.
 */
export const Markdown = memo(function Markdown({ children, onAccent }: Props) {
  return (
    <div className={`md text-sm${onAccent ? " md-on-accent" : ""}`} dir="auto">
      <ReactMarkdown
        // Thinking models write maths as LaTeX ($x \times y$, $$…$$); render it
        // rather than showing the raw markup.
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
        components={{
          // Links out of a chat answer are untrusted; never hand the opener a
          // live window reference.
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer nofollow" />
          ),
          // A wide table has to scroll inside the bubble rather than stretch it.
          table: ({ node, ...props }) => (
            <div className="md-table-wrap">
              <table {...props} />
            </div>
          ),
          code: ({ node, className, children, ...props }) => {
            if (className?.split(" ").includes("language-mermaid")) {
              return <MermaidDiagram source={String(children).replace(/\n$/, "")} />;
            }
            return <code className={className} {...props}>{children}</code>;
          },
          pre: ({ node, children, ...props }) => {
            const child = Children.count(children) === 1 ? Children.only(children) : null;
            if (isValidElement(child) && child.type === MermaidDiagram) return <>{child}</>;
            return <pre {...props}>{children}</pre>;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
