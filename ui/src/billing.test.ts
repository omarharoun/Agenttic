import { describe, expect, it } from "vitest";
import { creditsToMoney, currencySymbol, money, sharePct } from "./billing";

describe("money", () => {
  it("formats integer cents as dollars", () => {
    expect(money(2900)).toBe("$29.00");
    expect(money(0)).toBe("$0.00");
    expect(money(1)).toBe("$0.01");
  });

  it("drops decimals for whole-dollar amounts when compact", () => {
    expect(money(2900, "usd", true)).toBe("$29");
    expect(money(1000, "usd", true)).toBe("$10");
    // non-whole amounts keep decimals even when compact
    expect(money(1575, "usd", true)).toBe("$15.75");
  });

  it("uses the right currency symbol", () => {
    expect(money(500, "eur")).toBe("€5.00");
    expect(money(500, "gbp")).toBe("£5.00");
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("unknown")).toBe("$");
  });
});

describe("creditsToMoney", () => {
  it("treats 1 credit as 1 cent by default", () => {
    expect(creditsToMoney(500)).toBe("$5.00");
    expect(creditsToMoney(20000)).toBe("$200.00");
  });

  it("honours a non-default credit_cent_value", () => {
    expect(creditsToMoney(100, 2)).toBe("$2.00");
  });
});

describe("sharePct", () => {
  it("computes an integer percentage share", () => {
    expect(sharePct(17, 20)).toBe(85);
    expect(sharePct(1, 3)).toBe(33);
  });
  it("is 0 when the total is 0 (no division by zero)", () => {
    expect(sharePct(0, 0)).toBe(0);
  });
});
