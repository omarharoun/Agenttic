import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Recursively flatten a cell's React children back into plain text so we can
 *  tell a numeric column ("0.0021", "1234", "67%") from a label. Used only to
 *  pick text alignment — never rendered, so it can't inject anything. */
function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  // React element with children
  const props = (node as any)?.props;
  return props ? textOf(props.children) : "";
}

const NUMERIC = /^[$]?[-+]?[\d,]+(\.\d+)?\s*(ms|%|s)?$/;
const isNumeric = (n: ReactNode) => {
  const t = textOf(n).trim();
  return t !== "" && NUMERIC.test(t);
};

/**
 * Safe markdown renderer for credential-grade documents (run reports,
 * scorecards). GitHub-flavoured markdown via remark-gfm gives us the pipe
 * tables. Raw HTML is DISABLED — we deliberately do NOT add rehype-raw or
 * dangerouslySetInnerHTML, because the report can embed untrusted agent output,
 * so any `<script>`/`<img onerror>` in the source is rendered as inert text,
 * never as live markup. Tables are wrapped so they scroll on narrow screens,
 * and numeric cells right-align with tabular figures.
 */
export function Markdown({ children, className = "" }: {
  children: string;
  className?: string;
}) {
  return (
    <div className={`report-doc ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ node, ...props }) => (
            <div className="report-table-wrap">
              <table {...props} />
            </div>
          ),
          th: ({ node, children, ...props }) => (
            <th className={isNumeric(children) ? "num" : undefined} {...props}>
              {children}
            </th>
          ),
          td: ({ node, children, ...props }) => (
            <td className={isNumeric(children) ? "num" : undefined} {...props}>
              {children}
            </td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
