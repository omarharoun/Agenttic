import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { friendlyError } from "./scanError";

/** The scan funnel used to print "[object Object]" when the rate-limiter tripped:
 *  a 429 carries a structured {code,message,action} detail, and the old code
 *  String()-ed the object. These lock in graceful handling of the 429 (and any
 *  structured-detail error) without ever surfacing a raw parse/serialization
 *  artifact. */
describe("friendlyError", () => {
  it("shows the server's message for a rate-limit 429 (structured detail), not [object Object]", () => {
    const e = new ApiError("ignored", 429, {
      code: "rate_limited",
      message: "You're doing that too fast — give it a minute and try again.",
      action: "retry",
    });
    const f = friendlyError(e);
    expect(f.auth).toBe(false);
    expect(f.msg).toBe("You're doing that too fast — give it a minute and try again.");
    expect(f.msg).not.toMatch(/object Object/);
  });

  it("falls back to a calm rate-limit line when a 429 carries no message", () => {
    const f = friendlyError(new ApiError("429", 429, undefined));
    expect(f.auth).toBe(false);
    expect(f.msg).toMatch(/going a bit fast/i);
    expect(f.msg).not.toMatch(/object Object/);
  });

  it("routes a 401 into the auth (signup) flow with no error message", () => {
    const f = friendlyError(new ApiError("401 unauthenticated", 401, "nope"));
    expect(f.auth).toBe(true);
    expect(f.msg).toBe("");
  });

  it("surfaces a plain string detail and strips a leading status code", () => {
    const f = friendlyError(new ApiError("500 — the scan engine hiccuped", 500, "the scan engine hiccuped"));
    expect(f.auth).toBe(false);
    expect(f.msg).toBe("the scan engine hiccuped");
  });

  it("never yields [object Object] even for a bare structured error", () => {
    const f = friendlyError(new ApiError("boom", 502, { code: "generic", message: "gateway fell over" }));
    expect(f.msg).toBe("gateway fell over");
  });

  it("degrades to a generic line for an empty/unknown error", () => {
    const f = friendlyError(new Error(""));
    expect(f.auth).toBe(false);
    expect(f.msg).toBe("Something went wrong. Please try again.");
  });
});
