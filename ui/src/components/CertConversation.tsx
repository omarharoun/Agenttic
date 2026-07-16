/* ============================================================================
   The intake interview — the seamless front door to certification.

   Shaped like a coding-agent session: the transcript scrolls on top, and every
   answer happens in ONE fixed prompt box at the bottom — a numbered option
   list you can drive with ↑/↓, enter, or the number keys (space toggles in
   multi-select), plus a `›` free-text prompt line. The answer UI never moves;
   only the question above it changes.

   Beside the chat, the certification profile panel: every answer writes onto
   it (FOCUS marks accrue on the five quick-scan dimensions, the profile
   sentence composes), and when the endpoint lands the SAME panel becomes the
   live scan readout — rows flip from FOCUS to pending to pass/fail and the
   grade stamps in place. No wizard chrome, no page switch.

   Honesty note, by construction: the quick scan always runs every dimension
   of the safety battery. Your answers shape emphasis and the report's framing
   — they never silently narrow what is tested. The chat says so out loud, and
   the count it quotes is the live dimension count, not a hard-coded number.

   The auth seam: starting a scan signed-out saves the WHOLE conversation
   (answers + endpoint) to sessionStorage and offers sign-up inline as a chat
   turn. Coming back restores the interview and resumes the scan.
   ========================================================================== */

import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type ScanCheck, type ScanJob, type ScanPreview } from "../api";
import { badgeUrl, certUrl, gradeColor } from "../cert";
import { friendlyError } from "../scanError";
import { SCORE_MEANING } from "../workflow/templates";
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
  { id: "send",  chip: "Sends emails or messages", emph: ["no_secret_leak"] },
  { id: "code",  chip: "Executes code",            emph: ["tool_misuse_safety"] },
];

interface FearOpt { id: string; chip: string; emph: string[]; note?: string }
const FEARS: FearOpt[] = [
  { id: "leak",   chip: "It leaks something private",     emph: ["no_secret_leak"] },
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
  { criterion_id: "no_secret_leak",         label: "Keeps secrets safe",       critical: false },
  { criterion_id: "tool_misuse_safety",     label: "Uses tools safely",        critical: false },
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
              <span title={SCORE_MEANING}>
                Composite safety score {job!.result!.composite_score}/100
              </span>
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

/* ---- the prompt box (the session's one fixed answer surface) -------------- */

interface PromptOption { key: string; label: string; on?: boolean }

/** A numbered, keyboard-driven option list — the coding-session select menu.
 *  `sel` is the highlighted row; clicking or Enter picks; in multi mode the
 *  parent toggles on pick and confirms separately. */
function OptionList({ opts, sel, multi, onHover, onPick }: {
  opts: PromptOption[]; sel: number; multi?: boolean;
  onHover: (i: number) => void; onPick: (i: number) => void;
}) {
  return (
    <div className="cvp-opts" role="listbox" aria-multiselectable={multi || undefined}>
      {opts.map((o, i) => (
        <button key={o.key} type="button" role="option" aria-selected={i === sel}
                className={`cvp-opt ${i === sel ? "sel" : ""} ${o.on ? "on" : ""}`}
                onMouseEnter={() => onHover(i)} onClick={() => onPick(i)}>
          <span className="cvp-caret">{i === sel ? "❯" : " "}</span>
          <span className="cvp-num">{i + 1}.</span>
          {multi && <span className="cvp-check">{o.on ? "◉" : "○"}</span>}
          <span className="cvp-lab">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

/* ---- the conversation ----------------------------------------------------- */

export function CertConversation() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [step, setStep] = useState<StepId>("does");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [thinking, setThinking] = useState(false);
  const [answers, setAnswers] = useState<Answers>({ touch: [] });
  const [text, setText] = useState("");           // the prompt line (free text / URL)
  const [sel, setSel] = useState(0);              // highlighted option row
  const [agentName] = useState("");
  const [showAuth, setShowAuth] = useState(false);
  const [headerName, setHeaderName] = useState("Authorization");
  const [headerValue, setHeaderValue] = useState("");
  const [dims, setDims] = useState(FALLBACK_DIMS);
  // The demo agent runs on the tenant's own Anthropic key. We learn from the
  // preview whether a key is set; until we know, treat it as unset so we never
  // start a demo scan that would dead-end with a "add your key" error after
  // already promising the run. (Defaults closed = honest.)
  const [demoKeySet, setDemoKeySet] = useState(false);
  const [job, setJob] = useState<ScanJob | null>(null);
  const [needAuth, setNeedAuth] = useState(false);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const promptRef = useRef<HTMLDivElement | null>(null);
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
    api.scanPreview().then((p) => {
      if (p.dimensions.length) setDims(p.dimensions);
      setDemoKeySet(!!p.demo?.key_set);
    }).catch(() => {});

    let saved: SavedIntake | null = null;
    try { const raw = sessionStorage.getItem(STORE_KEY); if (raw) saved = JSON.parse(raw); } catch { /* ignore */ }
    if (saved) {
      sessionStorage.removeItem(STORE_KEY);
      setAnswers(saved.answers);
      setText(saved.url);
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

  // keep the newest turn in view; reset highlight + refocus the prompt per step
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [msgs, thinking, step]);
  useEffect(() => { setSel(0); setText((t) => (step === "target" ? t : "")); }, [step]);
  // Focus the answer surface whenever the prompt box is on screen. The box only
  // renders once the typing beat ends, and the no-input steps (touch/fear/done)
  // have no <input> — so focus the prompt box itself (tabIndex -1) to keep the
  // keyboard driving (↑↓/space/number/enter). Without this, focus falls to
  // <body> after a step change and the key handler never fires.
  useEffect(() => {
    if (thinking || step === "run") return;
    (inputRef.current ?? promptRef.current)?.focus();
  }, [step, thinking, needAuth]);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  /* ---- answer handlers ---- */

  const advanceDoes = (o?: DoesOpt, free?: string) => {
    if (o) { setAnswers((a) => ({ ...a, does: o.id })); you(o.said); }
    else   { setAnswers((a) => ({ ...a, doesText: free })); you(free!); }
    setStep("touch");
    say("Got it. What can it actually touch? Pick everything that applies.");
  };

  const toggleTouch = (id: string) =>
    setAnswers((a) => ({ ...a, touch: a.touch.includes(id) ? a.touch.filter((x) => x !== id) : [...a.touch, id] }));
  const confirmTouch = () => {
    const picked = TOUCH.filter((o) => answers.touch.includes(o.id));
    you(picked.length ? picked.map((o) => o.chip).join(", ") : "Nothing beyond chat.");
    setStep("fear");
    say("Now the important one — what's the failure that would actually scare you?");
  };

  const pickFear = (f: FearOpt) => {
    setAnswers((a) => ({ ...a, fear: f.id })); you(f.chip);
    const focus = f.emph.length
      ? `I've marked that FOCUS on your profile. To be clear: the quick scan always runs all ${dims.length} checks — your focus shapes the emphasis of the report, never what gets tested.`
      : "";
    say(f.note ? f.note : `Understood. ${focus}`, () => {
      setStep("target");
      say("Last thing: where does your agent live? Paste its API endpoint — or pick the demo agent below.");
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
        say(`Done. Grade ${g} — composite safety score ${j.result?.composite_score ?? "?"}/100 ` +
            `(weighted across dimensions; not the same as a pass rate). ` +
            (j.result?.grade_capped && j.result.cap_reason
              ? `Why not higher? ${j.result.cap_reason}`
              : "The panel has the dimension-by-dimension readout."));
      }
    }).catch((e) => { setErr(friendlyError(e).msg || "Lost the scan — please try again."); setStep("target"); });
  };

  const startScan = (target: "endpoint" | "demo", restored?: SavedIntake) => {
    const theUrl = (restored?.url ?? text).trim();
    if (target === "endpoint" && !theUrl) { setErr("Paste your agent's endpoint URL first."); return; }
    // Backstop the honest gate (covers the restore-after-auth autostart path):
    // never echo "Running the battery…" for a demo run we know will 400 for a
    // missing key — route the user to add their key instead.
    if (target === "demo" && !demoKeySet) {
      setStep("target");
      setErr("The demo runs on your own Anthropic key — add it in Settings, then run the demo.");
      return;
    }
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
    setStep("does"); setJob(null); setErr(""); setNeedAuth(false); setCopied(false);
    setAnswers({ touch: [] }); setText("");
    setMsgs([{ from: "a", text: "Fresh interview. What does this agent do?" }]);
  };

  /* ---- the prompt box model: options + actions for the current step ------ */

  const crt = job?.certificate;
  let opts: PromptOption[] = [];
  let multi = false;
  let placeholder = "";
  let hint = "↑↓ select · enter answer · or just type";
  if (step === "does") {
    opts = DOES.map((o) => ({ key: o.id, label: o.chip }));
    placeholder = "describe your agent in your own words…";
  } else if (step === "touch") {
    multi = true;
    opts = TOUCH.map((o) => ({ key: o.id, label: o.chip, on: answers.touch.includes(o.id) }));
    hint = "↑↓ select · space toggle · enter continue";
  } else if (step === "fear") {
    opts = FEARS.map((f) => ({ key: f.id, label: f.chip }));
    hint = "↑↓ select · enter answer";
  } else if (step === "target" && !needAuth) {
    opts = [
      { key: "demo", label: demoKeySet
          ? "Use the demo agent · runs on your Anthropic key"
          : "Use the demo agent — add your Anthropic key first" },
      { key: "auth", label: showAuth ? "Hide the auth header" : "Add an auth header", on: showAuth },
    ];
    placeholder = "https://your-agent.com/chat";
    hint = "paste your endpoint and press enter · ↑↓ for options";
  } else if (step === "target" && needAuth) {
    opts = [
      { key: "signup", label: "Create a free account" },
      { key: "login",  label: "Log in" },
    ];
    hint = "↑↓ select · enter go — your answers are saved";
  } else if (step === "done") {
    opts = [
      ...(crt ? [
        { key: "cert",  label: "Open the certificate" },
        { key: "badge", label: copied ? "Copied ✓ — README badge" : "Copy the README badge" },
      ] : []),
      { key: "again", label: "Scan another agent" },
    ];
    hint = "↑↓ select · enter run";
  }

  const pick = (i: number) => {
    const o = opts[i]; if (!o) return;
    if (step === "does") advanceDoes(DOES[i]);
    else if (step === "touch") { toggleTouch(o.key); setSel(i); }
    else if (step === "fear") pickFear(FEARS[i]);
    else if (step === "target" && !needAuth) {
      // The demo runs on the tenant's own Anthropic key. If none is set, send
      // them to Settings to add one instead of starting a run that dead-ends.
      if (o.key === "demo") { if (demoKeySet) startScan("demo"); else nav("/app/settings"); }
      else setShowAuth((s) => !s);
    }
    else if (step === "target" && needAuth) nav(o.key === "signup" ? "/signup?next=/scan" : "/login?next=/scan");
    else if (step === "done") {
      if (o.key === "cert" && crt) nav(`/certified/${crt.cert_id}`);
      else if (o.key === "badge" && crt) {
        navigator.clipboard?.writeText(
          `[![Tested with Agenttic](${badgeUrl(crt.cert_id)})](${certUrl(crt.cert_id)})`);
        setCopied(true); window.setTimeout(() => setCopied(false), 1600);
      }
      else if (o.key === "again") reset();
    }
  };

  const submitLine = () => {
    const t = text.trim();
    if (step === "does") { if (t) { advanceDoes(undefined, t); setText(""); } else pick(sel); }
    else if (step === "touch") confirmTouch();
    else if (step === "fear") pick(sel);
    else if (step === "target" && !needAuth) { if (t) startScan("endpoint"); else pick(sel); }
    else pick(sel);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (thinking || step === "run") return;
    const inInput = (e.target as HTMLElement).tagName === "INPUT";
    const typing = inInput && text.length > 0;
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, opts.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); submitLine(); }
    else if (e.key === " " && multi && !typing) { e.preventDefault(); pick(sel); }
    else if (!typing && /^[1-9]$/.test(e.key)) {
      const i = Number(e.key) - 1;
      if (i < opts.length) { e.preventDefault(); setSel(i); pick(i); }
    }
  };

  const hasLine = step === "does" || (step === "target" && !needAuth);

  /* ---- render ---- */

  return (
    <div className={`convo step-${step}`}>
      <div className="cv-chat">
        <div className="cv-scroll" aria-live="polite">
          {msgs.map((m, i) => (
            <div key={i} className={`cv-msg ${m.from === "a" ? "from-a" : "from-u"}`}>{m.text}</div>
          ))}
          {thinking && <div className="cv-msg from-a cv-typing" aria-label="Agenttic is typing"><i /><i /><i /></div>}
          {step === "run" && !thinking && (
            <div className="cv-msg from-a cv-runline">
              <span className="cv-spin" /> {job?.phase || "Starting the scan…"}
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* the ONE prompt box — every answer happens here, coding-session style */}
        {step !== "run" && !thinking && (
          <div className="cv-prompt" onKeyDown={onKey} ref={promptRef} tabIndex={-1}>
            {opts.length > 0 && (
              <OptionList opts={opts} sel={sel} multi={multi}
                          onHover={setSel} onPick={pick} />
            )}
            {multi && (
              <button type="button" className="cvp-continue" onClick={confirmTouch}>
                {answers.touch.length ? "That's everything →" : "Nothing beyond chat →"}
              </button>
            )}
            {hasLine && (
              <div className="cvp-line">
                <span className="cvp-mark">›</span>
                <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)}
                       type={step === "target" ? "url" : "text"}
                       inputMode={step === "target" ? "url" : "text"}
                       placeholder={placeholder} aria-label={placeholder} autoComplete="off" />
              </div>
            )}
            {step === "target" && !needAuth && showAuth && (
              <div className="cv-authrow">
                <input value={headerName} onChange={(e) => setHeaderName(e.target.value)}
                       placeholder="Authorization" aria-label="Header name" />
                <input type="password" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)}
                       placeholder="Bearer sk-…" aria-label="Header value" />
              </div>
            )}
            {err && <p className="cv-err">{err}</p>}
            <div className="cvp-hint">
              <span>{hint}</span>
              {step === "target" && !needAuth && (
                <span className="cvp-fine">~14 probes · endpoint needs no key · demo runs on your Anthropic key</span>
              )}
              {step === "done" && crt && (
                <span className="cvp-fine">quick scan — a fast screen, not a full audit</span>
              )}
            </div>
          </div>
        )}
      </div>

      <ProfilePanel dims={dims} answers={answers} job={job} phase={step} />
    </div>
  );
}
