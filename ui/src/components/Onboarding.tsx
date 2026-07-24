import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

/* ============================================================================
   First-run guided tutorial — the full journey to TEST and TRAIN an agent.

   An interactive, resumable checklist (not just a static strip): five concrete
   steps from "point at your agent" through "re-score to prove it improved",
   each with a one-line explanation and a button to the surface that does it.
   Progress persists in localStorage, so a returning first-timer picks up where
   they left off; two steps auto-tick from real account state (key set, first
   run), and clicking a step's action also marks it done. Skippable (dismiss),
   collapsible (minimize), and it congratulates + hides itself once complete.
   ========================================================================== */

const DISMISS_KEY = "agenttic_onboarding_dismissed";
const DONE_KEY = "agenttic_onboarding_done";        // JSON string[] of step ids
const COLLAPSE_KEY = "agenttic_onboarding_collapsed";

interface Step {
  id: string;
  title: string;
  body: string;
  cta: { to: string; label: string };
  alt?: { to: string; label: string }[];
}

const STEPS: Step[] = [
  {
    id: "point",
    title: "Point at your agent",
    body: "Add your Anthropic API key to run the built-in agents, or connect your own agent's endpoint. Your key is encrypted at rest and never shared.",
    cta: { to: "/app/settings?section=api-keys", label: "Add your key" },
    alt: [{ to: "/scan", label: "Connect an agent" }],
  },
  {
    id: "score",
    title: "Run a scored evaluation",
    body: "Score your agent against a suite or a quick safety scan — you get a real pass-rate with its sample size and confidence interval, not a vibe check.",
    cta: { to: "/app/build", label: "New evaluation" },
    alt: [{ to: "/scan", label: "Quick scan" }],
  },
  {
    id: "issues",
    title: "Read the Issues report",
    body: "See what's actually wrong — your agent's failures ranked worst-first, each with the evidence and a plain-language reason it failed.",
    cta: { to: "/app/issues", label: "Open Issues" },
  },
  {
    id: "fix",
    title: "Fix & train it",
    body: "Close the gaps: harden the failing cases into a regression suite, optimize the system prompt against them, or train the behavior in Training Camp.",
    cta: { to: "/app/training-camp", label: "Training Camp" },
    alt: [{ to: "/app/optimize", label: "Optimize" }, { to: "/app/hardening", label: "Harden" }],
  },
  {
    id: "rescore",
    title: "Re-score to prove it improved",
    body: "Run the same evaluation again and compare head-to-head — a paired significance test tells you whether the fix really moved the number.",
    cta: { to: "/app/compare", label: "Compare runs" },
    alt: [{ to: "/app/build", label: "Re-run evaluation" }],
  },
];

function readDone(): Set<string> {
  try {
    const raw = localStorage.getItem(DONE_KEY);
    return new Set<string>(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();
  }
}

export function Onboarding() {
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1");
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "1");
  const [done, setDone] = useState<Set<string>>(readDone);
  // steps auto-detected as complete from real account state (merged, read-only)
  const [auto, setAuto] = useState<Set<string>>(new Set());

  // Auto-tick the two steps we can verify cheaply: a key is set, and at least
  // one run exists. Best-effort — failures just leave them manually checkable.
  useEffect(() => {
    if (dismissed) return;
    let live = true;
    api.anthropicKeyStatus()
      .then((s) => { if (live && s.set) setAuto((a) => new Set(a).add("point")); })
      .catch(() => {});
    api.listExecutions()
      .then((r) => { if (live && r.length) setAuto((a) => new Set(a).add("score")); })
      .catch(() => {});
    return () => { live = false; };
  }, [dismissed]);

  if (dismissed) return null;

  const isDone = (id: string) => done.has(id) || auto.has(id);
  const completedCount = STEPS.filter((s) => isDone(s.id)).length;
  const allDone = completedCount === STEPS.length;

  const persistDone = (next: Set<string>) => {
    setDone(next);
    localStorage.setItem(DONE_KEY, JSON.stringify([...next]));
  };
  const toggle = (id: string) => {
    const next = new Set(done);
    if (next.has(id)) next.delete(id); else next.add(id);
    persistDone(next);
  };
  const markDone = (id: string) => {
    if (done.has(id)) return;
    persistDone(new Set(done).add(id));
  };
  const dismiss = () => { localStorage.setItem(DISMISS_KEY, "1"); setDismissed(true); };
  const setCollapse = (v: boolean) => {
    setCollapsed(v);
    localStorage.setItem(COLLAPSE_KEY, v ? "1" : "0");
  };

  // Collapsed pill — resumable at a glance, out of the way.
  if (collapsed && !allDone) {
    return (
      <button className="onboard-mini" onClick={() => setCollapse(false)}>
        <span className="onboard-mini-ic">◆</span>
        Getting started — {completedCount}/{STEPS.length} done
        <span className="onboard-mini-open">Resume →</span>
      </button>
    );
  }

  if (allDone) {
    return (
      <section className="onboard onboard-complete" aria-label="Getting started complete">
        <button className="onboard-x" onClick={dismiss} title="Dismiss">✕</button>
        <div className="onboard-head">
          <span className="eyebrow">Tutorial complete</span>
          <h2>You've run the whole loop 🎉</h2>
          <p>
            You've scored an agent, read its issues, fixed them, and re-scored to
            prove it improved. That's the full Score → Issues → Fix cycle — repeat
            it any time your agent changes.
          </p>
        </div>
        <button className="onboard-dismiss" onClick={dismiss}>Hide this</button>
      </section>
    );
  }

  const pct = Math.round((completedCount / STEPS.length) * 100);

  return (
    <section className="onboard" aria-label="Getting started tutorial">
      <button className="onboard-x" onClick={dismiss} title="Skip the tutorial">✕</button>
      <div className="onboard-head">
        <span className="eyebrow">Getting started · tutorial</span>
        <h2>Test <i>and</i> train your first agent</h2>
        <p>
          Five steps take you from pointing at an agent to proving it got better.
          Follow the thread — your progress is saved, so you can leave and come back.
        </p>
        <div className="onboard-progress" role="progressbar"
             aria-valuenow={completedCount} aria-valuemin={0} aria-valuemax={STEPS.length}>
          <span className="onboard-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="onboard-progress-lab">{completedCount} of {STEPS.length} complete</span>
      </div>

      <ol className="onboard-checklist">
        {STEPS.map((s, i) => {
          const complete = isDone(s.id);
          const locked = auto.has(s.id) && !done.has(s.id); // auto-verified
          return (
            <li className={`onboard-item ${complete ? "is-done" : ""}`} key={s.id}>
              <button className="onboard-check" onClick={() => !locked && toggle(s.id)}
                      aria-pressed={complete} disabled={locked}
                      title={locked ? "Detected as done from your account"
                                    : complete ? "Mark not done" : "Mark done"}>
                {complete ? "✓" : i + 1}
              </button>
              <div className="onboard-item-body">
                <h3>{s.title}</h3>
                <p>{s.body}</p>
                <div className="onboard-links">
                  <Link className="btn-primary onboard-cta" to={s.cta.to}
                        onClick={() => markDone(s.id)}>{s.cta.label}</Link>
                  {s.alt?.map((a) => (
                    <Link className="onboard-alt" key={a.to} to={a.to}
                          onClick={() => markDone(s.id)}>{a.label}</Link>
                  ))}
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      <div className="onboard-foot">
        <button className="onboard-dismiss" onClick={() => setCollapse(true)}>Minimize</button>
        <span className="onboard-foot-sep">·</span>
        <button className="onboard-dismiss" onClick={dismiss}>Skip tutorial</button>
      </div>
    </section>
  );
}
