/* Shared design-system primitives (SPEC-11 Step 51). One implementation each,
 * token-only (styles in ds.css). The landing route and the console both consume
 * these instead of the two divergent ad-hoc conventions they had before.
 */
import { useState, type ReactNode } from "react";

// ---- Button (solid / ghost) ----------------------------------------------
export function Button({
  children, variant = "solid", href, onClick, type = "button", className = "", ...rest
}: {
  children: ReactNode; variant?: "solid" | "ghost"; href?: string;
  onClick?: () => void; type?: "button" | "submit"; className?: string;
  [k: string]: unknown;
}) {
  const cls = `ds-btn ds-btn--${variant} ${className}`.trim();
  if (href) return <a className={cls} href={href} onClick={onClick} {...rest}>{children}</a>;
  return <button className={cls} type={type} onClick={onClick} {...rest}>{children}</button>;
}

// ---- Eyebrow --------------------------------------------------------------
export function Eyebrow({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`ds-eyebrow ${className}`.trim()}>{children}</div>;
}

// ---- SectionHeading -------------------------------------------------------
export function SectionHeading({
  eyebrow, title, sub, id,
}: { eyebrow?: ReactNode; title: ReactNode; sub?: ReactNode; id?: string }) {
  return (
    <header className="ds-sechead" id={id}>
      {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
      <h2 className="ds-sechead__h">{title}</h2>
      {sub && <p className="ds-sechead__sub">{sub}</p>}
    </header>
  );
}

// ---- CodeBlock (terminal with copy) --------------------------------------
export interface CodeLine { prompt?: string; text: string; comment?: string; }

export function CodeBlock({ lines, label }: { lines: CodeLine[]; label?: string }) {
  const [copied, setCopied] = useState(false);
  const plain = lines.map((l) => (l.prompt ? l.prompt + " " : "") + l.text).join("\n");
  const copy = () => {
    try {
      navigator.clipboard?.writeText(plain);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="ds-term" role="group" aria-label={label ?? "terminal commands"}>
      <button className="ds-term__copy" onClick={copy} aria-label="copy commands">
        {copied ? "copied" : "copy"}
      </button>
      <pre className="ds-term__body">
        {lines.map((l, i) => (
          <div className="ds-term__line" key={i}>
            {l.prompt && <span className="ds-term__p">{l.prompt} </span>}
            <span className="ds-term__t">{l.text}</span>
            {l.comment && <span className="ds-term__c">  {l.comment}</span>}
          </div>
        ))}
      </pre>
    </div>
  );
}

// ---- StatTile -------------------------------------------------------------
export function StatTile({
  tag, value, note,
}: { tag: ReactNode; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="ds-stat">
      <span className="ds-stat__tag">{tag}</span>
      <b className="ds-stat__v">{value}</b>
      {note && <span className="ds-stat__note">{note}</span>}
    </div>
  );
}

// ---- ComparisonTable ------------------------------------------------------
export interface CompColumn { key: string; header: ReactNode; highlight?: boolean; }
export interface CompRow { rowHeader: ReactNode; cells: Record<string, ReactNode>; }

export function ComparisonTable({ columns, rows, caption }: {
  columns: CompColumn[]; rows: CompRow[]; caption?: string;
}) {
  return (
    <div className="ds-cmp-wrap">
      <table className="ds-cmp">
        {caption && <caption className="ds-cmp__cap">{caption}</caption>}
        <thead>
          <tr>
            <th />
            {columns.map((c) => (
              <th key={c.key} className={c.highlight ? "ds-cmp__us" : ""}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="ds-cmp__rowh">{r.rowHeader}</td>
              {columns.map((c) => (
                <td key={c.key} className={c.highlight ? "ds-cmp__us" : ""}>{r.cells[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- FaqItem --------------------------------------------------------------
export function FaqItem({
  q, children, open = false,
}: { q: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="ds-faq" open={open}>
      <summary className="ds-faq__q">{q}</summary>
      <div className="ds-faq__a">{children}</div>
    </details>
  );
}
