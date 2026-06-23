import { describe, expect, it } from "vitest";
import {
  bandForIndex, badgeUrl, certUrl, dimensionLabel, embedSnippets,
  gradeColor, gradeLetter, statusView,
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

describe("dimension labels", () => {
  it("uses known labels and humanizes unknown keys", () => {
    expect(dimensionLabel("injection_robustness")).toBe("Prompt-injection robustness");
    expect(dimensionLabel("secret_leak")).toBe("Secret-leak resistance");
    expect(dimensionLabel("some_new_metric")).toBe("Some New Metric");
  });
});
