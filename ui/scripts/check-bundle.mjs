#!/usr/bin/env node
/* Bundle budget gate for the public landing route (SPEC-11 Step 53).
 * Fails the build if the JS the prerendered landing (dist/index.html) actually
 * loads exceeds the budget, gzipped. The heavy console (AppShell) is a separate
 * lazy chunk and must never leak into the landing's initial payload.
 *
 *   node ui/scripts/check-bundle.mjs
 */
import { readFileSync, existsSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;   // ui/
const INDEX = join(ROOT, "dist/index.html");
const BUDGET_KB = 150;

if (!existsSync(INDEX)) {
  console.error("✗ bundle budget: dist/index.html not found — run the build first.");
  process.exit(1);
}
const htmlSrc = readFileSync(INDEX, "utf8");
const scripts = [...new Set([...htmlSrc.matchAll(/\/assets\/[A-Za-z0-9_.-]+\.js/g)].map((m) => m[0]))];

let total = 0;
const rows = [];
for (const s of scripts) {
  const f = join(ROOT, "dist", s.replace(/^\//, ""));
  if (!existsSync(f)) continue;
  const gz = gzipSync(readFileSync(f)).length;
  total += gz;
  rows.push(`  ${s.padEnd(38)} ${(gz / 1024).toFixed(1)} KB gz`);
}
const totalKb = total / 1024;
console.log(`landing initial JS (gzipped):\n${rows.join("\n")}`);
console.log(`  total: ${totalKb.toFixed(1)} KB gz  (budget ${BUDGET_KB} KB)`);

// guard: the console shell must not be in the landing's initial payload.
if (scripts.some((s) => /AppShell/.test(s))) {
  console.error("✗ bundle budget: the console AppShell chunk is loaded by the landing — it must stay lazy.");
  process.exit(1);
}
if (totalKb > BUDGET_KB) {
  console.error(`✗ bundle budget: landing initial JS ${totalKb.toFixed(1)} KB gz exceeds ${BUDGET_KB} KB.`);
  process.exit(1);
}
console.log(`✓ bundle budget: landing under ${BUDGET_KB} KB gz.`);
