/* ============================================================================
   The intake interview — the seamless front door to certification.

   One surface, two halves that are really one machine: a conversation on the
   left (Agenttic asks one question at a time; you answer with a chip or free
   text) and the certification profile on the right. Every answer writes onto
   the profile — dimensions gain FOCUS marks, the profile sentence composes —
   and when the last answer (your endpoint) lands, the SAME panel becomes the
   live scan readout: rows flip from FOCUS to pending to pass/fail and the
   grade stamps in place. No wizard chrome, no page switch.

   Honesty note, by construction: the quick scan always runs all five safety
   dimensions. Your answers shape emphasis and the report's framing — they
   never silently narrow what is tested. The chat says so out loud.

   The auth seam: starting a scan signed-out saves the WHOLE conversation
   (answers + endpoint) to sessionStorage and offers sign-up inline as a chat
   turn. Coming back restores the interview and resumes the scan.
   ========================================================================== */

import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type ScanCheck, type ScanJob, type ScanPreview } from "../api";
import { badgeUrl, certUrl, gradeColor } from "../cert";
import { Seal } from "./Seal";

const STORE_KEY = "agenttic_intake_v1";
const POLL_MS = 700;

/* ---- the interview ------------------------------------------------------ */

type StepId = "does" | "touch" | "fear" | "target" | "run" | "done";

interface DoesOpt { id: string; chip: string; said: string; profile: string }
const DOES: DoesOpt[] = [
  { id: "support",  chip: "Handles customer support", said: "It handles customer support.", profile: "support agent" },
  { id: "code",     chip: "Writes code",              said: "It writes code.",              profile: "coding agent" },
  { id: "research", chip: "Research & browsing",      said: "It does research and browsing.", profile: "research agent" },
  { id: "ops",      chip: "Runs internal ops",        said: "It runs internal operations.",  profile: "ops agent" },
];

interface TouchOpt { id: string; chip: string; emph: string[] }
const TOUCH: TouchOpt[] = [
  { id: "chat",  chip: "It just chats",            emph: ["injection_robust", "harmful_action_refused"] },
  { id: "tools", chip: "Calls tools or APIs",      emph: ["tool_misuse_safety"] },
  { id: "data",  chip: "Reads private data",       emph: ["no_secret_leak"] },
  { id: "send",  chip: "Sends emails or messages", emph: ["no_exfiltration"] },
  { id: "code",  chip: "Executes code",            emph: ["tool_misuse_safety"] },
];

interface FearOpt { id: string; chip: string; emph: string[]; note?: string }
const FEARS: FearOpt[] = [
  { id: "leak",   chip: "It leaks something private",     emph: ["no_secret_leak", "no_exfiltration"] },
  { id: "inject", chip: "Someone manipulates it",         emph: ["injection_robust"] },
  { id: "harm",   chip: "It does something harmful",      emph: ["harmful_action_refused"] },
  { id: "tools",  chip: "It misuses its tools",           emph: ["tool_misuse_safety"] },
  { id: "wrong",  chip: "It's confidently wrong",         emph: [],
    note: "Straight answer: the quick scan is a safety screen and doesn't measure truthfulness. The full certification does — faithfulness and calibration are two of its seven metrics. I'll flag that on your report." },
];

/** Offline fallback so the panel renders even if /api/scan/preview is down.
 *  Labels mirror the engine's plain labels; the live preview replaces them. */
const FALLBACK_DIMS: ScanPreview["dimensions"] = [
  { criterion_id: "harmful_action_refused", label: "Refuses harmful requests", critical: true },
  { criterion_id: "injection_robust",       label: "Resists prompt injection", critical: true },
  { criterion_id: "no_secret_leak",         label: "Keeps secrets",            critical: false },
  { criterion_id: "no_exfiltration",        label: "No data exfiltration",     critical: false },
  { criterion_id: "tool_misuse_safety",     label: "Safe tool use",            critical: false },
];

interface Answers { does?: string; doesText?: string; touch: string[]; fear?: string }

/** Everything we need to restore the interview after an auth bounce. */
interface SavedIntake {
  answers: Answers; url: string; agentName: string;
  headerName: string; headerValue: string; useDemo: boolean; autostart: boolean;
}

interface Msg { from: "a" | "u"; text: string }

function profileSentence(a: Answers): string {
  const parts: string[] = [];
  const does = DOES.find((d) => d.id === a.does);
  parts.push(does ? does.profile : a.doesText ? `${a.doesText.slice(0, 32)} agent` : "your agent");
  if (a.touch.length) {
    const t = TOUCH.filter((o) => a.touch.includes(o.id)).map((o) => o.chip.toLowerCase());
    parts.push(t.join(" · "));
  }
  const fear = FEARS.find((f) => f.id === a.fear);
  if (fear && fear.emph.length) parts.push(`focus: ${fear.chip.toLowerCase().replace(/^it /, "").replace(/^someone /, "")}`);
  return parts.join(" — ");
}

function emphasisOf(a: Answers): Set<string> {
  const e = new Set<string>();
  TOUCH.filter((o) => a.touch.includes(o.id)).forEach((o) => o.emph.forEach((c) => e.add(c)));
  FEARS.filter((f) => f.id === a.fear).forEach((f) => f.emph.forEach((c) => e.add(c)));
  return e;
}

function friendlyError(e: any): { auth: boolean; msg: string } {
  const status = e?.status;
  const detail = String(e?.detail ?? e?.message ?? e ?? "");
  if (status === 401) return { auth: true, msg: "" };
  return { auth: false, msg: detail.replace(/^\d+\s*—?\s*/, "") || "Something went wrong. Please try again." };
}

/* ---- the profile panel (composes, then becomes the readout) -------------- */

function PanelRow({ d, emph, check }: {
  d: ScanPreview["dimensions"][number]; emph: boolean; check?: ScanCheck;
}) {
  let right: React.ReactNode;
  let state = "idle";
  if (check) {
    state = check.status;
    right = check.status === "pending" ? <span className="cv-spin" aria-label="checking" />
      : check.passed ? <span className="cv-ok">✓ {check.percent != null ? `${check.percent}%` : "pass"}</span>
      : check.status === "warn" ? <span className="cv-warn">! weak</span>
      : <span className="cv-fail">✗ fail</span>;
  } else if (emph) {
    state = "focus";
    right = <span className="cv-focus">FOCUS</span>;
  } else {
    right = <span className="cv-idle">standard</span>;
  }
  return (
    <li className={`cv-row is-${state}`}>
      <span className="cv-row-lab">
        {d.label}
        {d.critical && <i className="cv-crit" title="Critical dimension — failing it caps the grade">core</i>}
      </span>
      {right}
    </li>
  );
}

function ProfilePanel({ dims, answers, job, phase }: {
  dims: ScanPreview["dimensions"]; answers: Answers; job: ScanJob | null; phase: StepId;
}) {
  const emph = emphasisOf(answers);
  const checks = new Map((job?.checks ?? []).map((c) => [c.criterion_id, c]));
  const graded = phase === "done" && job?.result;
  return (
    <aside className="cv-panel" aria-label="Your certification profile">
      <div className="cv-panel-top">
        <span>CERTIFICATION PROFILE</span>
        <span className="cv-panel-mode">{phase === "run" ? "SCANNING" : graded ? "GRADED" : "COMPOSING"}</span>
      </div>

      {(phase === "run" || graded) && (
        <div className={`cv-seal ${graded ? "revealed" : "spinning"}`}>
          <Seal grade={graded ? job!.result!.grade : undefined} size={116} />
          {graded && (
            <div className="cv-verdict">
              <b style={{ color: gradeColor(job!.result!.grade) }}>Grade {job!.result!.grade}</b>
              <span>Safety score {job!.result!.composite_score}/100</span>
            </div>
          )}
          {phase === "run" && (
            <div className="cv-progress" role="progressbar"
                 aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
              <span style={{ width: `${Math.round((job?.progress ?? 0.05) * 100)}%` }} />
            </div>
          )}
        </div>
      )}

      <p className="cv-sentence">{profileSentence(answers)}</p>

      <ul className="cv-rows">
        {dims.map((d) => (
          <PanelRow key={d.criterion_id} d={d} emph={emph.has(d.criterion_id)}
                    check={checks.get(d.criterion_id)} />
        ))}
      </ul>

      <div className="cv-panel-foot">
        <span>profile cert-agent-safety-v1</span>
        <span>quick scan · ~14 probes</span>
      </div>
    </aside>
  );
}

/* ---- the conversation ----------------------------------------------------- */

export function CertConversation() {
  const [params] = useSearchParams();
  const [step, setStep] = useState<StepId>("does");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [thinking, setThinking] = useState(false);
  const [answers, setAnswers] = useState<Answers>({ touch: [] });
  const [doesText, setDoesText] = useState("");
  const [url, setUrl] = useState("");
  const [agentName, setAgentName] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [headerName, setHeaderName] = useState("Authorization");
  const [headerValue, setHeaderValue] = useState("");
  const [dims, setDims] = useState(FALLBACK_DIMS);
  const [job, setJob] = useState<ScanJob | null>(null);
  const [needAuth, setNeedAuth] = useState(false);
  const [err, setErr] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const endRef = useRef<HTMLDivElement | null>(null);
  const booted = useRef(false);

  /** Append an Agenttic turn with a brief "typing" beat (skipped for reduced motion). */
  const say = (text: string, after?: () => void) => {
    const instant = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (instant) { setMsgs((m) => [...m, { from: "a", text }]); after?.(); return; }
    setThinking(true);
    window.setTimeout(() => {
      setThinking(false);
      setMsgs((m) => [...m, { from: "a", text }]);
      after?.();
    }, 420);
  };
  const you = (text: string) => setMsgs((m) => [...m, { from: "u", text }]);

  // Boot: load real dimensions; restore an auth-bounced interview; honor a
  // landing-page pre-answer (?does=support) so the funnel has no repeated step.
  useEffect(() => {
    if (booted.current) return; booted.current = true;
    api.scanPreview().then((p) => p.dimensions.length && setDims(p.dimensions)).catch(() => {});

    let saved: SavedIntake | null = null;
    try { const raw = sessionStorage.getItem(STORE_KEY); if (raw) saved = JSON.parse(raw); } catch { /* ignore */ }
    if (saved) {
      sessionStorage.removeItem(STORE_KEY);
      setAnswers(saved.answers);
      setUrl(saved.url); setAgentName(saved.agentName);
      setHeaderName(saved.headerName); setHeaderValue(saved.headerValue);
      setMsgs([
        { from: "a", text: "Welcome back — I kept your answers. Picking up right where we left off." },
      ]);
      if (saved.autostart) { startScan(saved.useDemo ? "demo" : "endpoint", saved); return; }
      setStep("target");
      say("Last thing: where does your agent live? Paste its API endpoint, or run the demo agent.");
      return;
    }

    const pre = DOES.find((d) => d.id === params.get("does"));
    if (pre) {
      setAnswers((a) => ({ ...a, does: pre.id }));
      setMsgs([
        { from: "a", text: "Let's get your agent certified — four quick questions, then I run the scan while you watch." },
        { from: "a", text: "What does your agent do?" },
        { from: "u", text: pre.said },
      ]);
      setStep("touch");
      say("Got it. What can it actually touch? Pick everything that applies.");
      return;
    }

    setMsgs([{ from: "a", text: "Let's get your agent certified — four quick questions, then I run the scan while you watch." }]);
    say("First: what does your agent do?");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // keep the newest turn in view
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [msgs, thinking, step]);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  /* ---- answer handlers ---- */

  const pickDoes = (o: DoesOpt) => {
    setAnswers((a) => ({ ...a, does: o.id })); you(o.said); setStep("touch");
    say("Got it. What can it actually touch? Pick everything that applies.");
  };
  const submitDoesText = () => {
    const t = doesText.trim(); if (!t) return;
    setAnswers((a) => ({ ...a, doesText: t })); you(t); setStep("touch");
    say("Got it. What can it actually touch? Pick everything that applies.");
  };

  const toggleTouch = (id: string) =>
    setAnswers((a) => ({ ...a, touch: a.touch.includes(id) ? a.touch.filter((x) => x !== id) : [...a.touch, id] }));
  const submitTouch = () => {
    const picked = TOUCH.filter((o) => answers.touch.includes(o.id));
    you(picked.length ? picked.map((o) => o.chip).join(", ") : "Nothing beyond chat.");
    setStep("fear");
    say("Now the important one — what's the failure that would actually scare you?");
  };

  const pickFear = (f: FearOpt) => {
    setAnswers((a) => ({ ...a, fear: f.id })); you(f.chip);
    const focus = f.emph.length
      ? "I've marked that FOCUS on your profile. To be clear: the quick scan always runs all five checks — your focus shapes the emphasis of the report, never what gets tested."
      : "";
    say(f.note ? f.note : `Understood. ${focus}`, () => {
      setStep("target");
      say("Last thing: where does your agent live? Paste its API endpoint, or run the demo agent.");
    });
  };

  /* ---- the scan itself ---- */

  const poll = (scanId: string) => {
    api.scanStatus(scanId).then((j) => {
      setJob(j);
      if (j.status === "running") {
        timer.current = window.setTimeout(() => poll(scanId), POLL_MS);
      } else if (j.status === "error") {
        setErr(j.error || "The scan failed. Please try again."); setStep("target");
      } else {
        setStep("done");
        const g = j.result?.grade ?? "?";
        say(`Done. Grade ${g} — safety score ${j.result?.composite_score ?? "?"}/100. ` +
            (j.result?.grade_capped && j.result.cap_reason
              ? `Why not higher? ${j.result.cap_reason}`
              : "The panel has the dimension-by-dimension readout."));
      }
    }).catch((e) => { setErr(friendlyError(e).msg || "Lost the scan — please try again."); setStep("target"); });
  };

  const startScan = (target: "endpoint" | "demo", restored?: SavedIntake) => {
    const theUrl = (restored?.url ?? url).trim();
    if (target === "endpoint" && !theUrl) { setErr("Paste your agent's endpoint URL first."); return; }
    setErr(""); setNeedAuth(false); setJob(null);
    you(target === "demo" ? "Run it on the demo agent." : theUrl);
    setStep("run");
    say("Running the battery — watch the panel fill in. Each probe is a real request to your agent, tagged as a safety test.");
    const hv = (restored?.headerValue ?? headerValue).trim();
    api.startScan({
      target,
      ...(target === "endpoint" ? {
        url: theUrl,
        agent_name: (restored?.agentName ?? agentName).trim(),
        ...(hv ? { header_name: (restored?.headerName ?? headerName).trim(), header_value: hv } : {}),
      } : {}),
    }).then((r) => poll(r.scan_id))
      .catch((e) => {
        const f = friendlyError(e);
        if (f.auth) {
          try {
            const save: SavedIntake = {
              answers: restored?.answers ?? answers,
              url: theUrl, agentName: restored?.agentName ?? agentName,
              headerName: restored?.headerName ?? headerName,
              headerValue: hv, useDemo: target === "demo", autostart: true,
            };
            sessionStorage.setItem(STORE_KEY, JSON.stringify(save));
          } catch { /* ignore */ }
          setNeedAuth(true); setStep("target");
          say("One thing before I fire real probes: you need a free account — it takes about ten seconds, and I'll keep every answer and start the scan the moment you're back.");
        } else {
          setErr(f.msg); setStep("target");
        }
      });
  };

  const reset = () => {
    setStep("does"); setJob(null); setErr(""); setNeedAuth(false);
    setAnswers({ touch: [] }); setDoesText(""); setUrl("");
    setMsgs([{ from: "a", text: "Fresh interview. What does this agent do?" }]);
  };

  /* ---- render ---- */

  const crt = job?.certificate;
  return (
    <div className={`convo step-${step}`}>
      <div className="cv-chat" aria-live="polite">
        <div className="cv-scroll">
          {msgs.map((m, i) => (
            <div key={i} className={`cv-msg ${m.from === "a" ? "from-a" : "from-u"}`}>{m.text}</div>
          ))}
          {thinking && <div className="cv-msg from-a cv-typing" aria-label="Agenttic is typing"><i /><i /><i /></div>}

          {/* the answer area renders as the next thing in the thread */}
          {!thinking && step === "does" && (
            <div className="cv-answer">
              <div className="cv-chips">
                {DOES.map((o) => (
                  <button key={o.id} type="button" className="cv-chip" onClick={() => pickDoes(o)}>{o.chip}</button>
                ))}
              </div>
              <form className="cv-freetext" onSubmit={(e) => { e.preventDefault(); submitDoesText(); }}>
                <input value={doesText} onChange={(e) => setDoesText(e.target.value)}
                       placeholder="or say it in your own words…" aria-label="Describe your agent" />
                <button type="submit" className="cv-chip" disabled={!doesText.trim()}>Answer</button>
              </form>
            </div>
          )}

          {!thinking && step === "touch" && (
            <div className="cv-answer">
              <div className="cv-chips">
                {TOUCH.map((o) => (
                  <button key={o.id} type="button"
                          className={`cv-chip multi ${answers.touch.includes(o.id) ? "on" : ""}`}
                          aria-pressed={answers.touch.includes(o.id)}
                          onClick={() => toggleTouch(o.id)}>{o.chip}</button>
                ))}
              </div>
              <button type="button" className="cv-chip go" onClick={submitTouch}>
                {answers.touch.length ? "That's everything →" : "Nothing beyond chat →"}
              </button>
            </div>
          )}

          {!thinking && step === "fear" && (
            <div className="cv-chips cv-answer">
              {FEARS.map((f) => (
                <button key={f.id} type="button" className="cv-chip" onClick={() => pickFear(f)}>{f.chip}</button>
              ))}
            </div>
          )}

          {!thinking && step === "target" && !needAuth && (
            <div className="cv-answer">
              <form className="cv-freetext" onSubmit={(e) => { e.preventDefault(); startScan("endpoint"); }}>
                <input type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)}
                       placeholder="https://your-agent.com/chat" aria-label="Your agent's API endpoint" />
                <button type="submit" className="cv-chip go" disabled={!url.trim()}>Scan it</button>
              </form>
              <div className="cv-chips">
                <button type="button" className="cv-chip" onClick={() => startScan("demo")}>Use the demo agent</button>
                <button type="button" className="cv-chip quiet" onClick={() => setShowAuth((s) => !s)}
                        aria-expanded={showAuth}>{showAuth ? "− auth header" : "+ auth header"}</button>
              </div>
              {showAuth && (
                <div className="cv-authrow">
                  <input value={headerName} onChange={(e) => setHeaderName(e.target.value)}
                         placeholder="Authorization" aria-label="Header name" />
                  <input type="password" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)}
                         placeholder="Bearer sk-…" aria-label="Header value" />
                </div>
              )}
              {err && <p className="cv-err">{err}</p>}
              <p className="cv-fine">
                ~14 short safety probes, sent one at a time and graded. <b>No Anthropic key needed</b> —
                your agent runs on your own infrastructure.
              </p>
            </div>
          )}

          {!thinking && step === "target" && needAuth && (
            <div className="cv-answer cv-chips">
              <Link className="cv-chip go" to="/signup?next=/scan">Create a free account</Link>
              <Link className="cv-chip" to="/login?next=/scan">Log in</Link>
            </div>
          )}

          {!thinking && step === "done" && job && (
            <div className="cv-answer cv-wrap">
              {crt ? (
                <>
                  <p className="cv-fine">
                    Signed certificate minted — pinned to the exact agent version tested.
                    This grade is a <b>quick scan</b> (a fast screen, not a full audit).
                  </p>
                  <div className="cv-chips">
                    <Link className="cv-chip go" to={`/certified/${crt.cert_id}`}>Open the certificate</Link>
                    <button type="button" className="cv-chip"
                            onClick={() => navigator.clipboard?.writeText(
                              `[![Tested with Agenttic](${badgeUrl(crt.cert_id)})](${certUrl(crt.cert_id)})`)}>
                      Copy README badge
                    </button>
                    <button type="button" className="cv-chip quiet" onClick={reset}>Scan another agent</button>
                  </div>
                </>
              ) : (
                <>
                  {job.cert_note && <p className="cv-fine">{job.cert_note}</p>}
                  <div className="cv-chips">
                    <button type="button" className="cv-chip quiet" onClick={reset}>Scan another agent</button>
                  </div>
                </>
              )}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <ProfilePanel dims={dims} answers={answers} job={job} phase={step} />
    </div>
  );
}
