import { describe, expect, it } from "vitest";
import { hasTraceId, shortTraceId, traceId } from "./traces";

/** Guards the Traces console against the crash "can't access property 'slice',
 *  trace_id is undefined": every row is rendered through these helpers, so they
 *  must tolerate a missing / null id and an `id`-vs-`trace_id` shape mismatch. */

describe("shortTraceId", () => {
  it("shortens a normal 32-char trace id", () => {
    expect(shortTraceId({ trace_id: "abcdef0123456789abcdef0123456789" }))
      .toBe("abcdef012345");
  });

  it("does not throw when trace_id is missing, undefined, or null", () => {
    // the exact rows that used to crash the whole console
    expect(() => shortTraceId({} as any)).not.toThrow();
    expect(() => shortTraceId({ trace_id: undefined })).not.toThrow();
    expect(() => shortTraceId({ trace_id: null })).not.toThrow();
    expect(() => shortTraceId(undefined)).not.toThrow();
    expect(shortTraceId({})).toBe("(no id)");
    expect(shortTraceId({ trace_id: null })).toBe("(no id)");
  });

  it("reconciles a shape that uses `id` instead of `trace_id`", () => {
    expect(shortTraceId({ id: "fedcba9876543210" })).toBe("fedcba987654");
    expect(traceId({ id: "trace-42" })).toBe("trace-42");
  });
});

describe("hasTraceId", () => {
  it("is false for un-drillable rows and true otherwise", () => {
    expect(hasTraceId({})).toBe(false);
    expect(hasTraceId({ trace_id: null })).toBe(false);
    expect(hasTraceId({ trace_id: "t-1" })).toBe(true);
    expect(hasTraceId({ id: "t-1" })).toBe(true);
  });
});
