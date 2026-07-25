/* The certificate's measured scope must be visible, and must never flatter.
 *
 * A grade rendered without its narrowing is the misreading the platform exists
 * to prevent. Two failure modes are pinned here:
 *   1. an absent scope (pre-scope certificate) rendering as if coverage were met
 *   2. an unclosed / violated run rendering as though it were clean
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import {
  CertScopeDetail, CertScopeStrip, certScopeLabel,
} from "./verification";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;   // token-driven, so both themes are covered

const UNCLOSED = {
  properties_total: 8,
  properties_exercised: 5,
  trace_closure: 0.204,
  closure_target: 0.95,
  closed: false,
  violations: 2,
  unexercised_properties: [
    "never_pii_after_redaction",
    "never_cross_tenant_identifiers",
    "always_escalation_preceded_by_uncertainty",
  ],
};

const CLOSED = {
  properties_total: 8,
  properties_exercised: 8,
  trace_closure: 0.97,
  closure_target: 0.95,
  closed: true,
  violations: 0,
  unexercised_properties: [],
};

describe("certScopeLabel", () => {
  it("leads with how many properties actually ran", () => {
    expect(certScopeLabel(UNCLOSED)).toContain("5/8 properties exercised");
  });

  it("states NOT closed when closure was not met", () => {
    expect(certScopeLabel(UNCLOSED)).toContain("NOT closed");
    expect(certScopeLabel(UNCLOSED)).toContain("20.4%");
  });

  it("states closed only when it genuinely is", () => {
    const s = certScopeLabel(CLOSED);
    expect(s).toContain("coverage closed");
    expect(s).not.toContain("NOT closed");
  });

  it("says scope is not recorded rather than implying coverage", () => {
    const s = certScopeLabel(null);
    expect(s).toMatch(/not recorded/i);
    expect(s).not.toMatch(/closed/i);
  });
});

describe("CertScopeStrip", () => {
  it("marks an unclosed run as a caution, not a pass", () => {
    const out = html(<CertScopeStrip scope={UNCLOSED} />);
    expect(out).toContain("warn");
    expect(out).not.toContain("cert-scope-strip ok");
  });

  it("marks a missing scope as a caution too — absence is not coverage", () => {
    expect(html(<CertScopeStrip scope={null} />)).toContain("unknown");
  });

  it("marks a genuinely closed run as ok", () => {
    expect(html(<CertScopeStrip scope={CLOSED} />)).toContain("ok");
  });

  it("emits no raw hex colour (token-driven, so both themes are covered)", () => {
    expect(html(<CertScopeStrip scope={UNCLOSED} />)).not.toMatch(NO_HEX);
    expect(html(<CertScopeStrip scope={null} />)).not.toMatch(NO_HEX);
  });
});

describe("CertScopeDetail", () => {
  it("names every property that never ran", () => {
    const out = html(<CertScopeDetail scope={UNCLOSED} />);
    for (const p of UNCLOSED.unexercised_properties) expect(out).toContain(p);
    expect(out).toMatch(/did not have their|not evidence of anything/i);
  });

  it("shows the violation count and the closure target", () => {
    const out = html(<CertScopeDetail scope={UNCLOSED} />);
    expect(out).toContain("5 of 8");
    expect(out).toContain("20.4%");
    expect(out).toContain("95% target");
  });

  it("tells a reader to treat an unrecorded scope as unverified", () => {
    const out = html(<CertScopeDetail scope={null} />);
    expect(out).toMatch(/unverified rather than as full coverage/i);
  });

  it("lists nothing under never-exercised when everything ran", () => {
    const out = html(<CertScopeDetail scope={CLOSED} />);
    expect(out).not.toMatch(/Never exercised/i);
  });

  it("emits no raw hex colour", () => {
    expect(html(<CertScopeDetail scope={UNCLOSED} />)).not.toMatch(NO_HEX);
  });
});

describe("artifact naming — a screen is never a credential", () => {
  it("treats a scan_report as not a certificate", async () => {
    const { isCertificate, artifactNoun } = await import("./cert");
    const report = { artifact: "scan_report" as const, certified: false };
    expect(isCertificate(report)).toBe(false);
    expect(artifactNoun(report)).toBe("scan report");
  });

  it("treats an explicit certificate as one", async () => {
    const { isCertificate, artifactNoun } = await import("./cert");
    const c = { artifact: "certificate" as const, certified: true };
    expect(isCertificate(c)).toBe(true);
    expect(artifactNoun(c)).toBe("certificate");
  });

  it("defaults to certificate so pre-existing artifacts still read correctly", async () => {
    const { isCertificate } = await import("./cert");
    expect(isCertificate({})).toBe(true);
  });

  it("honours certified:false even without the artifact label", async () => {
    const { isCertificate } = await import("./cert");
    expect(isCertificate({ certified: false })).toBe(false);
  });
});
