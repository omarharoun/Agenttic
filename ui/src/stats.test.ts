import { describe, expect, it } from "vitest";
import { ciLabel, money, ms, pct, wilsonInterval, wilsonLower } from "./stats";

describe("wilsonInterval", () => {
  it("returns a zeroed interval for n=0", () => {
    expect(wilsonInterval(0, 0)).toEqual({ low: 0, high: 0, phat: 0, n: 0 });
  });

  it("brackets the point estimate", () => {
    const iv = wilsonInterval(7, 10);
    expect(iv.phat).toBeCloseTo(0.7, 6);
    expect(iv.low).toBeLessThan(0.7);
    expect(iv.high).toBeGreaterThan(0.7);
    expect(iv.low).toBeGreaterThanOrEqual(0);
    expect(iv.high).toBeLessThanOrEqual(1);
  });

  it("matches the backend wilson_lower_bound formula (7/10)", () => {
    // Reference value from src/ascore/camp/trainer.py at z=1.96.
    expect(wilsonLower(7, 10)).toBeCloseTo(0.3968, 3);
  });

  it("narrows as n grows for the same rate", () => {
    const small = wilsonInterval(70, 100);
    const big = wilsonInterval(700, 1000);
    expect(big.high - big.low).toBeLessThan(small.high - small.low);
  });
});

describe("formatters", () => {
  it("pct shows — for null and a percent otherwise", () => {
    expect(pct(null)).toBe("—");
    expect(pct(0.5)).toBe("50%");
  });

  it("money distinguishes a real 0 from missing data", () => {
    expect(money(null)).toBe("—");
    expect(money(0)).toBe("$0.0000");
    expect(money(0.01234)).toBe("$0.0123");
  });

  it("ms distinguishes a real 0 from missing data", () => {
    expect(ms(null)).toBe("n/a");
    expect(ms(0)).toBe("0 ms");
    expect(ms(123.6)).toBe("124 ms");
  });

  it("ciLabel renders a compact interval", () => {
    expect(ciLabel(wilsonInterval(7, 10))).toMatch(/^\d+–\d+%$/);
  });
});
