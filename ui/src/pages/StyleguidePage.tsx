import type { ComponentType } from "react";
import { PageHeader } from "../components/ui";
import * as Icons from "../icons";
import type { IconProps } from "../icons";

/* SPEC-4 17.3 — EXEMPT from the state trio: a static catalogue rendered from
   in-module constants (the icon exports, token list). It fetches nothing, so it
   is never loading/empty/error.

   ============================================================================
   Internal style guide (SPEC-4 17.1).

   Reachable at /app/styleguide, behind the same app auth as every other /app
   route. It is the living catalogue of the icon system: every named glyph, the
   colour tokens, and the key component states — so the set stays coherent as it
   grows and reviewers can eyeball the whole family at once. Not a primary nav
   item; linked from the "More" group.
   ========================================================================== */

/** Every exported icon component, by name. We filter the module's exports down
 *  to the render-able icon components (Icon*, Ico*, HexMark) so the grid always
 *  reflects the actual export surface — add an icon, it shows up here. */
const ICONS: [string, ComponentType<IconProps>][] = (Object.entries(Icons)
  .filter(([name, val]) =>
    typeof val === "function"
    && (name.startsWith("Icon") || name.startsWith("Ico") || name === "HexMark"))
  .map(([name, val]) => [name, val as ComponentType<IconProps>]) as [string, ComponentType<IconProps>][])
  .sort((a, b) => a[0].localeCompare(b[0]));

const TOKENS: { name: string; varName: string }[] = [
  { name: "accent", varName: "--accent" },
  { name: "accent-soft", varName: "--accent-soft" },
  { name: "text", varName: "--text" },
  { name: "muted", varName: "--muted" },
  { name: "faint", varName: "--faint" },
  { name: "border", varName: "--border" },
  { name: "panel", varName: "--panel" },
  { name: "panel-2", varName: "--panel-2" },
  { name: "ok", varName: "--ok" },
  { name: "wait", varName: "--wait" },
  { name: "fail", varName: "--fail" },
];

export function StyleguidePage() {
  return (
    <div className="page styleguide">
      <PageHeader
        title="Style guide"
        subtitle={`Icon system, colour tokens and component states. ${ICONS.length} icons in the set.`}
      />

      <section className="sg-section">
        <h2 className="sg-h2">Icons</h2>
        <p className="sg-lede">
          One coherent stroke set — 24px viewBox, 1.6px stroke, <code>currentColor</code>,
          decorative by default. This grid is generated from the live export
          surface of <code>src/icons</code>.
        </p>
        <div className="sg-icon-grid">
          {ICONS.map(([name, Ico]) => (
            <div className="sg-icon-cell" key={name}>
              <div className="sg-icon-glyph"><Ico size={24} /></div>
              <code className="sg-icon-name">{name}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="sg-section">
        <h2 className="sg-h2">Status glyphs</h2>
        <div className="sg-row">
          <span className="sg-status"><Icons.StatusIcon tone="ok" /> valid / pass</span>
          <span className="sg-status"><Icons.StatusIcon tone="wait" /> expired / warning</span>
          <span className="sg-status"><Icons.StatusIcon tone="fail" /> revoked / fail</span>
        </div>
      </section>

      <section className="sg-section">
        <h2 className="sg-h2">Colour tokens</h2>
        <div className="sg-token-grid">
          {TOKENS.map((t) => (
            <div className="sg-token" key={t.varName}>
              <span className="sg-swatch" style={{ background: `var(${t.varName})` }} />
              <span className="sg-token-name">{t.name}</span>
              <code className="sg-token-var">{t.varName}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="sg-section">
        <h2 className="sg-h2">Component states</h2>
        <div className="sg-row">
          <button className="btn-primary"><Icons.IconPlus size={15} /> Primary</button>
          <button className="btn-ghost"><Icons.IconRefresh size={15} /> Ghost</button>
          <button className="icon-btn" aria-label="Settings"><Icons.IconSettings size={16} /></button>
          <span className="status-chip succeeded"><Icons.IconCheck size={13} /> succeeded</span>
          <span className="status-chip failed"><Icons.IconClose size={13} /> failed</span>
        </div>
      </section>
    </div>
  );
}
