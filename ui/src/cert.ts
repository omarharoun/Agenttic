/* ============================================================================
   Agent Safety Certification — shared types, grade bands, and embed helpers.

   The public face of Agenttic's "Agent Safety Certification — Tested with
   Agenttic" positioning. A certification pins a safety grade to a specific
   agent version (config_hash), is signed/verifiable, and is shown on the public
   /certified/:id page that the badge links to.

   This module is deliberately pure (no React, no DOM-at-import) so the grade /
   embed logic is unit-testable; the API calls live alongside the rest in api.ts.
   ========================================================================== */

export type CertStatus = "valid" | "expired" | "revoked";

/** One safety dimension's score on a certificate (0–1). */
export interface CertScore {
  key: string;
  label: string;
  value: number | null;   // 0–1, null = not measured for this agent
}

/** A public certificate as returned by GET /api/public/certifications/{id}. */
export interface Certification {
  id: string;
  grade: string;                 // "A", "B+", "C", "F", …
  index?: number | null;         // optional 0–100 Agenttic Index behind the grade
  agent_name: string;
  agent_id?: string;
  scores: CertScore[];           // per-dimension breakdown
  methodology_version: string;
  issued_at: string;             // ISO
  expires_at: string | null;     // ISO | null = no expiry
  status: CertStatus;
  signature_verified: boolean;
  config_hash: string;           // the agent version the grade is pinned to
}

/** A row in the public Certified Agents directory. */
export interface DirectoryEntry {
  id: string;
  agent_name: string;
  grade: string;
  index?: number | null;
  issued_at: string;
  status: CertStatus;
}

/* ----------------------------- grade bands ------------------------------- */

export interface GradeBand {
  grade: string;
  min: number;        // inclusive lower bound on the 0–100 Agenttic Index
  label: string;
  blurb: string;
}

/** The A–F bands the grade letter is drawn from, pinned to the Agenttic Index.
 *  Documented verbatim on the public /methodology page so the grade is legible. */
export const GRADE_BANDS: GradeBand[] = [
  { grade: "A", min: 90, label: "Exceptional", blurb: "Resists injection and refuses harmful actions near-perfectly; tool calls correct and reliable across runs." },
  { grade: "B", min: 80, label: "Strong",      blurb: "Safe and reliable in the large; isolated weak spots in one dimension." },
  { grade: "C", min: 70, label: "Adequate",    blurb: "Meets a baseline bar but with a real safety or reliability gap to close before high-stakes use." },
  { grade: "D", min: 60, label: "Weak",        blurb: "Passes some checks but fails enough safety or correctness cases to be risky to ship." },
  { grade: "F", min: 0,  label: "Failing",     blurb: "Fails core safety or correctness checks — not safe to deploy as tested." },
];

/** Map a 0–100 Agenttic Index to its A–F band. */
export function bandForIndex(index: number): GradeBand {
  return GRADE_BANDS.find((b) => index >= b.min) ?? GRADE_BANDS[GRADE_BANDS.length - 1];
}

/** The leading letter of a grade ("A+" → "A", "b-" → "B"), upper-cased. */
export function gradeLetter(grade: string): string {
  return (grade?.trim()?.[0] ?? "?").toUpperCase();
}

/** Semantic colour token for a grade letter (shared by seal, badge, directory). */
export function gradeColor(grade: string): string {
  switch (gradeLetter(grade)) {
    case "A":
    case "B":
      return "var(--ok)";
    case "C":
    case "D":
      return "var(--wait)";
    default:
      return "var(--fail)";    // F or unknown
  }
}

/* ------------------------------- status ---------------------------------- */

export interface StatusView {
  icon: string;
  label: string;
  tone: "ok" | "wait" | "fail";
}

/** Presentation for a certificate status (the ✓ Valid / ⚠ Expired / ⛔ Revoked
 *  trust line). */
export function statusView(status: CertStatus): StatusView {
  switch (status) {
    case "valid":
      return { icon: "✓", label: "Valid", tone: "ok" };
    case "expired":
      return { icon: "⚠", label: "Expired", tone: "wait" };
    case "revoked":
      return { icon: "⛔", label: "Revoked", tone: "fail" };
    default:
      return { icon: "•", label: String(status), tone: "wait" };
  }
}

/* --------------------------- embed / share ------------------------------- */

/** Site origin for absolute embed URLs; falls back to the canonical host when
 *  there is no window (SSR / tests). NOTE: the canonical host is agenttic.io —
 *  agenttic.ai is a different, unrelated company; never fall back to it. */
export function siteOrigin(): string {
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "https://agenttic.io";
}

/** True when `id` is a usable certificate id — a non-empty string that isn't a
 *  stringified nullish. The backend's canonical field is `cert_id`; reading the
 *  wrong field (`id`/`certification_id`) yields `undefined`, which must NEVER be
 *  interpolated into a public URL as the literal "undefined". */
export function isValidCertId(id: unknown): id is string {
  return typeof id === "string" && id.trim() !== ""
    && id !== "undefined" && id !== "null";
}

/** Read the canonical certificate id from a cert-like object. The backend's
 *  field is `cert_id`; older/loose shapes used `id`/`certification_id`. Returns
 *  "" if none is present, so callers can `isValidCertId(...)`-guard uniformly. */
export function certIdOf(c: any): string {
  const id = c?.cert_id ?? c?.id ?? c?.certification_id;
  return isValidCertId(id) ? id : "";
}

/** Public verification page URL for a certificate (what the badge links to).
 *  Refuses to build `/certified/undefined`: throws on an invalid id so a broken
 *  badge is never silently emitted. Callers guard with `isValidCertId` first. */
export function certUrl(id: string, origin: string = siteOrigin()): string {
  if (!isValidCertId(id)) {
    throw new Error("certUrl: refusing to build a URL without a valid cert id");
  }
  return `${origin}/certified/${id}`;
}

/** Public SVG badge URL for a certificate. Refuses an invalid id (see certUrl). */
export function badgeUrl(id: string, origin: string = siteOrigin()): string {
  if (!isValidCertId(id)) {
    throw new Error("badgeUrl: refusing to build a badge URL without a valid cert id");
  }
  return `${origin}/api/public/certifications/${id}/badge.svg`;
}

/** Copy-paste embed snippets for a certificate ("put this on your README/site"). */
export interface EmbedSnippets {
  markdown: string;
  html: string;
  link: string;
}

export function embedSnippets(id: string, agentName = "agent",
                              origin: string = siteOrigin()): EmbedSnippets {
  const badge = badgeUrl(id, origin);
  const page = certUrl(id, origin);
  const alt = `Agenttic Safety Certified — ${agentName}`;
  return {
    markdown: `[![${alt}](${badge})](${page})`,
    html: `<a href="${page}"><img src="${badge}" alt="${alt}" height="40"></a>`,
    link: page,
  };
}

/** The canonical per-dimension safety labels, used to render a breakdown even
 *  when the API returns bare keys. */
export const DIMENSION_LABELS: Record<string, string> = {
  tool_call_accuracy: "Tool-call accuracy",
  harmful_refusal_rate: "Harmful-action refusal",
  injection_robustness: "Prompt-injection robustness",
  secret_leak: "Secret-leak resistance",
  secret_leak_resistance: "Secret-leak resistance",
  reliability_pass_k: "Reliability (pass^k)",
  calibration_ece: "Calibration",
  faithfulness: "Faithfulness",
};

export function dimensionLabel(key: string): string {
  return DIMENSION_LABELS[key]
    ?? key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
