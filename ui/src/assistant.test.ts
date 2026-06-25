import { describe, expect, it } from "vitest";
import {
  buildPreviewTurn, DEFAULT_POSTURE, normalizeApproval, normalizePosture,
  normalizeSession, postureLine, riskTone, toolLabel,
} from "./assistant";

describe("toolLabel", () => {
  it("maps known tools and humanizes unknown ones", () => {
    expect(toolLabel("web.fetch")).toBe("Open a web page");
    expect(toolLabel("calculator")).toBe("Do math");
    expect(toolLabel("some_new_tool")).toBe("Some New Tool");
    expect(toolLabel(undefined)).toBe("a tool");
  });
});

describe("riskTone", () => {
  it("maps risk levels to Noor tones", () => {
    expect(riskTone("high")).toBe("fail");
    expect(riskTone("medium")).toBe("wait");
    expect(riskTone("low")).toBe("ok");
    expect(riskTone(undefined)).toBe("ok");
  });
});

describe("postureLine", () => {
  it("renders the safe-by-default differentiator line", () => {
    expect(postureLine(DEFAULT_POSTURE)).toBe(
      "Sandboxed · no file or credential access · sensitive actions need your approval",
    );
  });
  it("reflects relaxed posture honestly", () => {
    const line = postureLine({
      ...DEFAULT_POSTURE, file_access: true, approval_required: false,
    });
    expect(line).toContain("no credential access");
    expect(line).not.toContain("need your approval");
  });
});

describe("normalizeApproval", () => {
  it("returns null for empty input", () => {
    expect(normalizeApproval(null)).toBeNull();
    expect(normalizeApproval(undefined)).toBeNull();
  });
  it("builds a friendly title from tool + target and defaults risk for fetch", () => {
    const a = normalizeApproval({ action_id: "act_1", tool: "web.fetch", url: "https://x.com" });
    expect(a?.action_id).toBe("act_1");
    expect(a?.target).toBe("https://x.com");
    expect(a?.title).toContain("open a web page");
    expect(a?.title).toContain("https://x.com");
    expect(a?.risk).toBe("medium");
  });
  it("respects an explicit title and risk", () => {
    const a = normalizeApproval({ id: "x", tool: "calc", title: "Run a calc", risk: "low" });
    expect(a?.title).toBe("Run a calc");
    expect(a?.risk).toBe("low");
  });
});

describe("normalizePosture", () => {
  it("falls back to safe defaults when absent", () => {
    expect(normalizePosture(null)).toEqual(DEFAULT_POSTURE);
  });
  it("merges partial posture onto safe defaults and reads cert id aliases", () => {
    const p = normalizePosture({ certificate_id: "c_9", tools: ["web.search"] });
    expect(p.sandboxed).toBe(true);
    expect(p.file_access).toBe(false);
    expect(p.cert_id).toBe("c_9");
    expect(p.tools).toEqual([{ name: "web.search", label: "Search the web" }]);
  });
});

describe("normalizeSession", () => {
  it("normalizes messages, posture and derives awaiting_approval from a pending action", () => {
    const s = normalizeSession({
      id: "sess_1",
      messages: [
        { role: "user", text: "hi" },
        { role: "assistant", content: "hello", steps: [{ tool: "web.fetch", summary: "Opened a page" }] },
      ],
      pending_approval: { action_id: "a1", tool: "web.fetch", url: "https://x" },
      posture: { grade: "A", cert_id: "c_1" },
    });
    expect(s.session_id).toBe("sess_1");
    expect(s.messages).toHaveLength(2);
    expect(s.messages[1].steps?.[0].summary).toBe("Opened a page");
    expect(s.status).toBe("awaiting_approval");
    expect(s.pending?.action_id).toBe("a1");
    expect(s.posture.cert_id).toBe("c_1");
  });
  it("degrades gracefully on an empty payload", () => {
    const s = normalizeSession({}, "fallback_id");
    expect(s.session_id).toBe("fallback_id");
    expect(s.status).toBe("idle");
    expect(s.messages).toEqual([]);
    expect(s.posture).toEqual(DEFAULT_POSTURE);
  });
});

describe("buildPreviewTurn", () => {
  it("gates a web request behind an approval", () => {
    const turn = buildPreviewTurn("Look up today's headline");
    expect(turn.pending).not.toBeNull();
    expect(turn.pending?.tool).toBe("web.fetch");
    expect(turn.afterAllow.length).toBeGreaterThan(0);
    expect(turn.denyAnswer).toContain("won't");
  });
  it("runs math locally with no approval", () => {
    const turn = buildPreviewTurn("how much if I save $45 a week");
    expect(turn.pending).toBeNull();
    expect(turn.steps[0].tool).toBe("calculator");
    expect(turn.answer).toContain("$2,340");
  });
  it("answers general questions without tools or approval", () => {
    const turn = buildPreviewTurn("explain prompt injection simply");
    expect(turn.pending).toBeNull();
    expect(turn.steps[0].kind).toBe("thought");
  });
  it("does not mistake a hyphenated number (e.g. '3-hour') for arithmetic", () => {
    const turn = buildPreviewTurn("Help me plan a focused 3-hour study block this afternoon.");
    expect(turn.steps[0].kind).toBe("thought");
    expect(turn.steps[0].tool).toBeUndefined();
  });
});
