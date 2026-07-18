#!/usr/bin/env node
/* SPEC-4 Step 21 — production bar: app-shell initial-JS bundle budget.
 *
 * Measures the gzip size of the INITIAL JavaScript the browser must download
 * and execute before the app is interactive — i.e. the Vite entry chunk plus
 * every chunk it statically (synchronously) imports. Lazy route chunks
 * (`dynamicImports` in the manifest) are deliberately excluded: they load on
 * demand at their route and are not part of the app-shell cost.
 *
 * FAILS (exit 1) if the measured size exceeds BUDGET_KB. The number and the
 * budget are always printed so a CI failure is self-explanatory.
 *
 * The measurement is driven off `dist/.vite/manifest.json`, which is the
 * authoritative chunk graph Vite emits — so it stays correct as chunks are
 * split or renamed, without hard-coding filenames.
 *
 * Run after `npm run build`:  node scripts/bundle-budget.mjs
 */
import { gzipSync } from "node:zlib";
import { readFileSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = join(__dirname, "..");
const DIST = join(UI_ROOT, "dist");
const MANIFEST = join(DIST, ".vite", "manifest.json");

/* Budget for the app-shell initial JS, gzipped.
 *
 * Current measured initial JS (2026-07): ~98 KB gz (single entry chunk, no
 * static imports — every page/console route is lazy-loaded). The 200 KB gz
 * budget is the SPEC-4 Step-21 production bar and leaves generous headroom;
 * if the real initial bundle ever grows past this, CI fails here rather than
 * silently shipping a heavy shell. */
const BUDGET_KB = 200;
const BUDGET_BYTES = BUDGET_KB * 1024;

function fail(msg) {
  console.error(`\n[31m✗ bundle-budget: ${msg}[0m\n`);
  process.exit(1);
}

if (!existsSync(MANIFEST)) {
  fail(`no build manifest at ${MANIFEST}. Run \`npm run build\` first.`);
}

const manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));

/* Find the entry: the manifest record with isEntry === true. vite-react-ssg
 * keys the entry off "index.html". */
const entryKey = Object.keys(manifest).find((k) => manifest[k]?.isEntry);
if (!entryKey) fail("could not locate the entry chunk (isEntry) in the manifest.");

/* Walk the STATIC import graph from the entry. `imports` = synchronous imports
 * that ship with the shell; `dynamicImports` = lazy chunks we intentionally
 * skip. Some manifest records list "index.html" itself inside `imports`
 * (self/entry reference) — ignore any key without an emitted `.file`. */
const seen = new Set();
const chunkFiles = new Set();

function walk(key) {
  if (seen.has(key)) return;
  seen.add(key);
  const rec = manifest[key];
  if (!rec) return;
  if (rec.file && rec.file.endsWith(".js")) chunkFiles.add(rec.file);
  for (const dep of rec.imports ?? []) walk(dep);
}
walk(entryKey);

if (chunkFiles.size === 0) fail("resolved zero JS chunks for the app shell.");

let totalRaw = 0;
let totalGz = 0;
const rows = [];
for (const file of [...chunkFiles].sort()) {
  const abs = join(DIST, file);
  if (!existsSync(abs)) fail(`manifest references a missing chunk: ${file}`);
  const buf = readFileSync(abs);
  const raw = statSync(abs).size;
  const gz = gzipSync(buf).length;
  totalRaw += raw;
  totalGz += gz;
  rows.push({ file, raw, gz });
}

const kb = (n) => (n / 1024).toFixed(1);

console.log("\napp-shell initial JS (entry + static imports, gzipped):");
for (const r of rows) {
  console.log(`  ${r.file}  ${kb(r.gz)} KB gz  (${kb(r.raw)} KB raw)`);
}
console.log(
  `\n  total: ${kb(totalGz)} KB gz  (${kb(totalRaw)} KB raw)` +
    `   budget: ${BUDGET_KB} KB gz`,
);

if (totalGz > BUDGET_BYTES) {
  fail(
    `initial JS ${kb(totalGz)} KB gz exceeds the ${BUDGET_KB} KB gz budget ` +
      `by ${kb(totalGz - BUDGET_BYTES)} KB. Split a route chunk out of the ` +
      `shell or lift the budget deliberately.`,
  );
}

console.log(
  `\n[32m✓ within budget — ${kb(totalGz)} KB gz ≤ ${BUDGET_KB} KB gz` +
    ` (${kb(BUDGET_BYTES - totalGz)} KB headroom)[0m\n`,
);
