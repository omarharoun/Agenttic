import { describe, expect, it } from "vitest";
import {
  bandForIndex, badgeUrl, certIdOf, certUrl, dimensionLabel, embedSnippets,
  gradeColor, gradeLetter, indexFromCert, isValidCertId, normalizeScores,
  statusView,
} from "./cert";

describe("grade bands", () => {
  it("maps the Index to the right A–F band", () => {
    expect(bandForIndex(95).grade).toBe("A");
    expect(bandForIndex(90).grade).toBe("A");
    expect(bandForIndex(89).grade).toBe("B");
    expect(bandForIndex(72).grade).toBe("C");
    expect(bandForIndex(60).grade).toBe("D");
    expect(bandForIndex(59).grade).toBe("F");
    expect(bandForIndex(0).grade).toBe("F");
  });

  it("extracts and upper-cases the leading grade letter", () => {
    expect(gradeLetter("A+")).toBe("A");
    expect(gradeLetter("b-")).toBe("B");
    expect(gradeLetter("")).toBe("?");
  });

  it("colours grades by safety tier", () => {
    expect(gradeColor("A")).toBe("var(--ok)");
    expect(gradeColor("B+")).toBe("var(--ok)");
    expect(gradeColor("C")).toBe("var(--wait)");
    expect(gradeColor("D")).toBe("var(--wait)");
    expect(gradeColor("F")).toBe("var(--fail)");
    expect(gradeColor("")).toBe("var(--fail)");
  });
});

describe("status view", () => {
  it("renders each status with icon + tone", () => {
    expect(statusView("valid")).toMatchObject({ icon: "✓", tone: "ok" });
    expect(statusView("expired")).toMatchObject({ icon: "⚠", tone: "wait" });
    expect(statusView("revoked")).toMatchObject({ icon: "⛔", tone: "fail" });
  });
});

describe("embed helpers", () => {
  const origin = "https://example.com";

  it("builds absolute cert + badge URLs", () => {
    expect(certUrl("c_123", origin)).toBe("https://example.com/certified/c_123");
    expect(badgeUrl("c_123", origin))
      .toBe("https://example.com/api/public/certifications/c_123/badge.svg");
  });

  it("produces markdown, html, and link snippets pointing at the badge + page", () => {
    const s = embedSnippets("c_123", "Acme Agent", origin);
    expect(s.link).toBe("https://example.com/certified/c_123");
    expect(s.markdown).toContain("/api/public/certifications/c_123/badge.svg");
    expect(s.markdown).toContain("(https://example.com/certified/c_123)");
    expect(s.markdown).toContain("Acme Agent");
    expect(s.html).toContain("<img src=\"https://example.com/api/public/certifications/c_123/badge.svg\"");
    expect(s.html).toContain("href=\"https://example.com/certified/c_123\"");
  });
});

describe("cert id guard (never build /certified/undefined)", () => {
  it("validates real ids and rejects nullish / stringified-nullish", () => {
    expect(isValidCertId("cert_abc123")).toBe(true);
    expect(isValidCertId("")).toBe(false);
    expect(isValidCertId("  ")).toBe(false);
    expect(isValidCertId(undefined)).toBe(false);
    expect(isValidCertId(null)).toBe(false);
    expect(isValidCertId("undefined")).toBe(false);
    expect(isValidCertId("null")).toBe(false);
  });

  it("reads the canonical cert_id (backend field), falling back to id/certification_id", () => {
    expect(certIdOf({ cert_id: "cert_1" })).toBe("cert_1");
    expect(certIdOf({ id: "cert_2" })).toBe("cert_2");
    expect(certIdOf({ certification_id: "cert_3" })).toBe("cert_3");
    expect(certIdOf({ agent_id: "no-id-here" })).toBe("");
    expect(certIdOf(undefined)).toBe("");
  });

  it("refuses to build a URL or badge from an invalid id", () => {
    expect(() => certUrl(undefined as any)).toThrow();
    expect(() => certUrl("undefined")).toThrow();
    expect(() => badgeUrl("" as any)).toThrow();
    expect(() => embedSnippets(undefined as any, "agent")).toThrow();
  });
});

describe("Agenttic Index / scan safety-score reconciliation", () => {
  // Regression: the scan view showed "Safety score 97.6/100" while the
  // certificate rounded the SAME composite_score to an integer and showed
  // "Agenttic Index 98". The Index must match the scan at one-decimal precision.
  it("shows composite_score at one decimal, matching the scan headline", () => {
    expect(indexFromCert({ composite_score: 97.6 })).toBe(97.6);
    expect(indexFromCert({ composite_score: 82.4 })).toBe(82.4);
  });

  it("does not round the composite up to a different integer", () => {
    // 97.6 must never surface as 98 (the reported contradiction).
    expect(indexFromCert({ composite_score: 97.6 })).not.toBe(98);
  });

  it("keeps whole-number scores clean", () => {
    expect(indexFromCert({ composite_score: 100 })).toBe(100);
    expect(indexFromCert({ composite_score: 90 })).toBe(90);
  });

  it("accepts a 0–1 composite and scales it to 0–100 at one decimal", () => {
    expect(indexFromCert({ composite_score: 0.976 })).toBe(97.6);
  });

  it("prefers an explicit index but still normalises its precision", () => {
    expect(indexFromCert({ index: 88.25, composite_score: 12 })).toBe(88.3);
  });

  it("is null when there is no numeric composite", () => {
    expect(indexFromCert({})).toBeNull();
    expect(indexFromCert({ composite_score: null })).toBeNull();
  });

  it("lands the reconciled index in the same grade band as the scan", () => {
    expect(bandForIndex(indexFromCert({ composite_score: 97.6 })!).grade).toBe("A");
  });
});

describe("per-dimension breakdown carries through to the certificate", () => {
  // Regression: the public API publishes the breakdown under `dimensions`
  // (each {criterion_id, label, score}), but the cert page read `scores` and so
  // always claimed "No per-dimension breakdown was published". The four scored
  // dimensions the scan produced must render on the certificate.
  const publicCert = {
    composite_score: 97.6,
    dimensions: [
      { criterion_id: "harmful_action_refused", label: "Harmful-action refusal", score: 1.0, critical: true },
      { criterion_id: "injection_robust", label: "Prompt-injection robustness", score: 0.95, critical: true },
      { criterion_id: "no_secret_leak", label: "Secret-leak resistance", score: 0.97 },
      { criterion_id: "tool_misuse_safety", label: "Tool-misuse safety", score: 0.98 },
    ],
  };

  it("maps the published dimensions array (criterion_id/label/score)", () => {
    const scores = normalizeScores(publicCert);
    expect(scores).toHaveLength(4);
    expect(scores[0]).toEqual({
      key: "harmful_action_refused",
      label: "Harmful-action refusal",
      value: 1.0,
    });
    expect(scores.map((s) => s.key)).toEqual([
      "harmful_action_refused", "injection_robust", "no_secret_leak", "tool_misuse_safety",
    ]);
  });

  it("no longer yields an empty breakdown when dimensions are present", () => {
    // cert page shows the "No per-dimension breakdown" note iff length === 0.
    expect(normalizeScores(publicCert).length).not.toBe(0);
  });

  it("preserves the same dimension count the scan scored", () => {
    expect(normalizeScores(publicCert)).toHaveLength(publicCert.dimensions.length);
  });

  it("still honours the legacy `scores` array shape", () => {
    const scores = normalizeScores({
      scores: [{ key: "injection_robustness", value: 0.9 }],
    });
    expect(scores).toEqual([
      { key: "injection_robustness", label: "Prompt-injection robustness", value: 0.9 },
    ]);
  });

  it("still honours the object-map shape", () => {
    const scores = normalizeScores({ scores: { injection_robustness: 0.9 } });
    expect(scores).toEqual([
      { key: "injection_robustness", label: "Prompt-injection robustness", value: 0.9 },
    ]);
  });

  it("honestly reports no breakdown only when none was published", () => {
    expect(normalizeScores({})).toEqual([]);
    expect(normalizeScores({ dimensions: [] })).toEqual([]);
  });

  it("marks a dimension with no numeric score as not-measured (null)", () => {
    const scores = normalizeScores({ dimensions: [{ criterion_id: "faithfulness" }] });
    expect(scores[0]).toEqual({ key: "faithfulness", label: "Faithfulness", value: null });
  });
});

describe("dimension labels", () => {
  it("uses known labels and humanizes unknown keys", () => {
    expect(dimensionLabel("injection_robustness")).toBe("Prompt-injection robustness");
    expect(dimensionLabel("secret_leak")).toBe("Secret-leak resistance");
    expect(dimensionLabel("some_new_metric")).toBe("Some New Metric");
  });
});
