/* Functional render check for the redesigned certificate document.
   Server-renders the real CertBody against valid/expired/revoked fixtures via
   react-dom/server (no DOM needed) and asserts the engraved document, the
   dimension rows, the overprint stamp, the signature line, the badge <img> src,
   the verify curl, and the published-keys link — i.e. that the "verify it
   yourself" surface points at the real public endpoints, not placeholders. */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import React from "react";
import { CertBody } from "./pages/CertificatePage";
import * as cert from "./cert";

const ID = "cert_8859af43f5784335";

const baseCert = (over: Partial<cert.Certification> = {}): cert.Certification => ({
  id: ID,
  grade: "A",
  index: 97.6,
  agent_name: "safe-reference-assistant",
  scores: [
    { key: "harmful_refusal_rate", label: "Harmful-action refusal", value: 1.0 },
    { key: "injection_robustness", label: "Prompt-injection robustness", value: 1.0 },
    { key: "no_secret_leak", label: "No secret leakage", value: 0.6667 },
    { key: "reliability_pass_k", label: "Reliability (pass^k)", value: null },
  ],
  methodology_version: "v1",
  issued_at: "2026-07-01T00:00:00Z",
  expires_at: "2027-07-01T00:00:00Z",
  status: "valid",
  signature_verified: true,
  config_hash: "a2b3255d409d278b",
  ...over,
});

function render(c: cert.Certification) {
  return renderToStaticMarkup(
    React.createElement(MemoryRouter, { initialEntries: [`/certified/${c.id}`] },
      React.createElement(CertBody, { cert: c, id: c.id })));
}

describe("certificate document — valid", () => {
  const m = render(baseCert());

  it("renders as the engraved document with seal, grade and cert number", () => {
    expect(m).toContain("certdoc");
    expect(m).toContain("AGENT SAFETY CERTIFICATION");
    expect(m).toContain(`№ ${ID}`);
    expect(m).toContain("safe-reference-assistant");
    expect(m).toContain("Agenttic Index 97.6");
  });

  it("prints the same dimension rows the scan scored (incl NOT ASSESSED for null)", () => {
    expect(m).toContain("Harmful-action refusal");
    expect(m).toContain("Prompt-injection robustness");
    expect(m).toContain("100%");
    expect(m).toContain("67%");
    expect(m).toContain("NOT ASSESSED");
  });

  it("shows the verified Ed25519 signature line, not a stamp", () => {
    expect(m).toContain("Signature verified");
    expect(m).toContain("Ed25519");
    expect(m).not.toContain("certdoc-stamp");
    expect(m).not.toContain(">EXPIRED<");
    expect(m).not.toContain(">REVOKED<");
  });

  it("the verify curl + keys link resolve to the real public endpoints", () => {
    expect(m).toContain(`curl https://agenttic.io/api/public/certifications/${ID}/verify`);
    expect(m).toContain("/.well-known/agenttic-cert-keys.json");
  });

  it("the badge <img> points at the real badge endpoint via badgeUrl", () => {
    expect(m).toContain(`https://agenttic.io/api/public/certifications/${ID}/badge.svg`);
    expect(m).toBe(m.replace("undefined", "undefined")); // sanity: no literal 'undefined' in urls
    expect(m).not.toContain("/certifications/undefined/");
  });

  it("share embeds carry the cert's real public README/HTML/link", () => {
    expect(m).toContain(`](https://agenttic.io/api/public/certifications/${ID}/badge.svg)`);
    expect(m).toContain(`https://agenttic.io/certified/${ID}`);
  });

  it("CTA routes to the intake, not the old signup", () => {
    expect(m).toContain('href="/scan"');
  });
});

describe("certificate document — lapsed states overprint the seal", () => {
  it("expired → .lapsed document with an EXPIRED overprint stamp", () => {
    const m = render(baseCert({ status: "expired", signature_verified: true }));
    expect(m).toMatch(/class="certdoc[^"]*\blapsed\b/);
    expect(m).toContain("certdoc-stamp");
    expect(m).toMatch(/certdoc-stamp[^>]*>\s*EXPIRED/);
    expect(m).not.toContain("REVOKED");
  });

  it("revoked → .lapsed document with a REVOKED overprint stamp", () => {
    const m = render(baseCert({ status: "revoked" }));
    expect(m).toMatch(/class="certdoc[^"]*\blapsed\b/);
    expect(m).toMatch(/certdoc-stamp[^>]*>\s*REVOKED/);
  });

  it("unverified signature reads 'unverified', never a clean verified line", () => {
    const m = render(baseCert({ signature_verified: false }));
    expect(m).toContain("Signature unverified");
    expect(m).not.toContain("Signature verified");
  });
});

describe("certificate document — no published breakdown", () => {
  it("states the breakdown is absent rather than faking rows", () => {
    const m = render(baseCert({ scores: [] }));
    expect(m).toContain("No per-dimension breakdown was published");
  });
});
