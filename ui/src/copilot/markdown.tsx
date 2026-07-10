/* ============================================================================
   Tiny, dependency-free Markdown renderer for Copilot answers.

   The platform ships no Markdown library and we don't want to add one just for
   the panel. This renders the small subset the Copilot actually emits —
   paragraphs, headings, unordered/ordered lists, fenced + inline code, bold,
   italic, and links — as React elements. It never uses dangerouslySetInnerHTML,
   so model output cannot inject HTML.

   Links are the one interactive bit: an in-app path ("/methodology",
   "/app/settings") renders as a router <Link> (and closes the panel via
   onNavigate) so the Copilot can deep-link the user around; anything external
   opens in a new, rel="noopener" tab.
   ========================================================================== */

import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

/* --- inline: bold / italic / code / links -------------------------------- */

const INLINE = /(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;

function renderInline(text: string, onNavigate?: () => void): ReactNode[] {
  const out: ReactNode[] = [];
  const parts = text.split(INLINE);
  parts.forEach((part, i) => {
    if (!part) return;
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (link) {
      const [, label, href] = link;
      out.push(<MdLink key={i} href={href} label={label} onNavigate={onNavigate} />);
    } else if (part.startsWith("`") && part.endsWith("`")) {
      out.push(<code key={i} className="cp-code">{part.slice(1, -1)}</code>);
    } else if (part.startsWith("**") && part.endsWith("**")) {
      out.push(<strong key={i}>{part.slice(2, -2)}</strong>);
    } else if (part.startsWith("*") && part.endsWith("*")) {
      out.push(<em key={i}>{part.slice(1, -1)}</em>);
    } else {
      out.push(<Fragment key={i}>{part}</Fragment>);
    }
  });
  return out;
}

function MdLink({ href, label, onNavigate }: {
  href: string; label: string; onNavigate?: () => void;
}) {
  // internal deep-link → router navigation (closes the drawer); external → new tab
  const internal = href.startsWith("/") && !href.startsWith("//");
  if (internal) {
    return (
      <Link className="cp-link" to={href} onClick={() => onNavigate?.()}>
        {label}
      </Link>
    );
  }
  return (
    <a className="cp-link" href={href} target="_blank" rel="noopener noreferrer">
      {label} ↗
    </a>
  );
}

/* --- block level ---------------------------------------------------------- */

export function Markdown({ text, onNavigate }: {
  text: string; onNavigate?: () => void;
}) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    if (line.trimStart().startsWith("```")) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        buf.push(lines[i]); i++;
      }
      i++; // closing fence
      blocks.push(<pre key={key++} className="cp-pre"><code>{buf.join("\n")}</code></pre>);
      continue;
    }

    // blank line
    if (line.trim() === "") { i++; continue; }

    // heading
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const Tag = (`h${Math.min(level + 2, 6)}`) as "h3" | "h4" | "h5";
      blocks.push(<Tag key={key++} className="cp-h">{renderInline(h[2], onNavigate)}</Tag>);
      i++;
      continue;
    }

    // list (unordered - / * , or ordered 1. )
    const isItem = (s: string) => /^\s*([-*]|\d+\.)\s+/.test(s);
    if (isItem(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items: ReactNode[] = [];
      while (i < lines.length && isItem(lines[i])) {
        const content = lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "");
        items.push(<li key={items.length}>{renderInline(content, onNavigate)}</li>);
        i++;
      }
      blocks.push(ordered
        ? <ol key={key++} className="cp-list">{items}</ol>
        : <ul key={key++} className="cp-list">{items}</ul>);
      continue;
    }

    // paragraph — gather consecutive non-blank, non-structural lines
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" &&
           !lines[i].trimStart().startsWith("```") &&
           !/^(#{1,3})\s+/.test(lines[i]) && !isItem(lines[i])) {
      para.push(lines[i]); i++;
    }
    blocks.push(<p key={key++} className="cp-p">{renderInline(para.join(" "), onNavigate)}</p>);
  }

  return <>{blocks}</>;
}
