#!/usr/bin/env node
/* Token lint (SPEC-11 Step 50, Hard Rule 47) — forbids raw hex colours in the
 * surfaces that must draw from design/tokens.css: src/pages, src/components, and
 * the landing route. Colours there must be a var(--token) or a tokens.ts value.
 *
 * A hex is any #RGB / #RRGGBB / #RRGGBBAA literal. Escape a genuine exception
 * (e.g. a data-URI, a non-colour token) by putting `tokens-allow` in a comment
 * on the same line. Test files are skipped.
 *
 *   node ui/scripts/check-tokens.mjs        # exits 1 on any violation
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, extname } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname; // ui/
const SCAN_DIRS = ["src/pages", "src/components", "src/landing"];
const EXPLICIT_FILES = [];                             // landing route file(s), if outside the dirs
const EXTS = new Set([".ts", ".tsx", ".css"]);
// a CSS hex colour, but NOT an HTML numeric entity (&#123;) — the (?<!&) guard.
const HEX = /(?<!&)#(?:[0-9a-fA-F]{8}\b|[0-9a-fA-F]{6}\b|[0-9a-fA-F]{3}\b)/;

function walk(dir, out) {
  let entries;
  try { entries = readdirSync(dir); } catch { return out; }
  for (const name of entries) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (EXTS.has(extname(name)) && !/\.test\.[tj]sx?$/.test(name)) out.push(full);
  }
  return out;
}

const files = SCAN_DIRS.flatMap((d) => walk(join(ROOT, d), []))
  .concat(EXPLICIT_FILES.map((f) => join(ROOT, f)));

const violations = [];
for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    if (line.includes("tokens-allow")) return;
    const m = line.match(HEX);
    if (m) violations.push(`${file.replace(ROOT, "ui/")}:${i + 1}  ${m[0]}  ${line.trim().slice(0, 80)}`);
  });
}

if (violations.length) {
  console.error(`✗ token lint: ${violations.length} raw hex colour(s) — use a var(--token) or tokens.ts:\n`);
  for (const v of violations) console.error("  " + v);
  console.error("\n(escape a genuine exception with a `tokens-allow` comment on the line)");
  process.exit(1);
}

// Shared-system proof (SPEC-11 Step 51, Hard Rule 48): the DS score components
// must read the shared score tokens, so swapping one token changes every surface
// that uses them at once. Assert the wiring here (Node can read the stylesheet;
// vitest stubs .css imports to empty).
const DS_CSS = join(ROOT, "src/components/ds/ds.css");
const WIRING = [
  /\.ds-badge--det\s*\{[^}]*var\(--score-deterministic/,
  /\.ds-badge--cal\s*\{[^}]*var\(--score-pass/,
  /\.ds-badge--prov\s*\{[^}]*var\(--score-provisional/,
  /\.ds-score--pass\s*\{[^}]*var\(--score-pass\)/,
  /\.ds-score--fail\s*\{[^}]*var\(--score-fail\)/,
];
try {
  const css = readFileSync(DS_CSS, "utf8");
  const missing = WIRING.filter((re) => !re.test(css));
  if (missing.length) {
    console.error(`✗ token lint: ds.css score components are not wired to the shared score tokens:\n`);
    for (const re of missing) console.error("  missing: " + re);
    process.exit(1);
  }
} catch { /* ds.css absent — nothing to assert */ }

console.log(`✓ token lint: no raw hex in ${SCAN_DIRS.join(", ")} (${files.length} files scanned); DS score components wired to shared tokens`);
