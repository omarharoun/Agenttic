import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { gradeColor, indexFromCert, normalizeScores } from "../cert";
import { IcoRail, IcoBus, IcoShield } from "../components/Icons";
import { SiteNav } from "../components/SiteNav";
import {
  Button, Eyebrow, SectionHeading, ScorecardCard, ProvenanceBadge, FaqItem,
} from "../components/ds";
import {
  SPINE, GAP, PRODUCT_STATS, WHY_NOW, SAMPLE_METRICS, SAMPLE_ROWS, FAQ, ON_TOP,
} from "../landing/data";
import "../landing/landing.css";

/* ============================================================================
   The public landing route — the "trust layer" narrative (stage 2).

   Record -> Evaluate -> Assert -> Certify. Six sections against the previous
   nineteen, because the old page was 14.4 viewports and its length was
   whitespace and repetition rather than argument (measured: 19 sections x 168px
   of padding = 3,192px, 23% of the page).

   Two rules this page is built to, both enforced elsewhere and both easy to
   break by accident here:

   * Every colour, size and space comes from design/tokens.css. No raw hex —
     `npm run lint:tokens` fails the build on one.
   * No figure on this page is a customer metric. Each is either SAMPLE DATA
     from one recorded run, labelled where it renders, or a count read off this
     source tree. Fabricated social proof is a hard rule, and a landing page is
     exactly where it creeps in.

   Deliberately absent: "replay" and any version-diff affordance. `cdv.replay()`
   replays a frozen regression scenario, not a production run against sandboxed
   tools, and per-version assertion history has no backend at all — so the page
   does not promise either.
   ========================================================================== */

/** Short legend labels for the certified dimensions (keyed on the backend's
 *  dimension keys); anything unknown falls back to the first word of its label. */
const METRIC_SHORT: Record<string, string> = {
  tool_call_accuracy: "tool-call",
  reliability_pass_k: "reliability",
  faithfulness: "faithful",
  harmful_refusal_rate: "refusal",
  injection_robustness: "injection",
  calibration_ece: "calibration",
  no_secret_leak: "secrets",
  secret_leak: "secrets",
  secret_leak_resistance: "secrets",
  no_exfiltration: "data",
  tool_misuse_safety: "tool safety",
};

/** A live metric point on the 0–340 × 0–132 ruled field (y inverted: lower y =
 *  higher score). Built from the featured certificate's REAL dimension scores. */
interface MetricPoint { key: string; label: string; value: number; x: number; y: number }

function toPoints(scores: { key: string; label: string; value: number | null }[]): MetricPoint[] {
  const measured = scores.filter((s) => s.value != null);
  const n = measured.length;
  if (n === 0) return [];
  return measured.map((s, i) => {
    const value = Math.round((s.value as number) * 100);
    const x = n === 1 ? 168 : Math.round(24 + (i * 288) / (n - 1));
    const y = Math.round(124 - (value / 100) * 115);   // 100 → 9, 0 → 124
    return {
      key: s.key,
      label: METRIC_SHORT[s.key] ?? s.label.split(/[\s(]/)[0].toLowerCase(),
      value, x, y,
    };
  });
}

/** The featured REAL certification shown in the hero instrument. */
interface LiveCert {
  certId: string;
  agentName: string;
  grade: string;
  index: number | null;
  methodology: string;
  points: MetricPoint[];
}

/** Published methodologies referenced across the suites. Every one has a real
 *  dataset adapter in the engine (src/ascore/metrics/datasets/*); several run
 *  on seed/sample splits, disclosed on the methodology page. */
const PROVENANCE = [
  "BFCL", "τ-bench", "AgentHarm", "AgentDojo",
  "InjecAgent", "AssistantBench", "GAIA", "SWE-bench Verified",
];

/** The three-step measurement procedure. */
const PROCEDURE: { pk: string; h: string; p: string; code: string }[] = [
  { pk: "01 · CALIBRATE", h: "Aim & pin",
    p: "Give an endpoint, or wrap your agent in two lines. Everything binds to that exact version — model, prompt, tools.",
    code: "graph = trace(graph)   # or --url" },
  { pk: "02 · TEST", h: "Run the battery",
    p: "Real safety and capability suites, many runs per case, with an elicitation probe that catches agents behaving differently when they sense a test.",
    code: "agenttic certify -p cert-agent-safety-v1" },
  { pk: "03 · STAMP", h: "Grade & sign",
    p: "A grade, a coverage table, and a content-hashed, signed dossier — verifiable by anyone, tied to that agent version.",
    code: "agenttic dossier verify ./dossier.json" },
];

/** The seven-row spec sheet: metric · method · weight. */
const SPEC: { m: string; s: ReactNode; w: string }[] = [
  { m: "Tool-call accuracy", w: "0.28",
    s: <>Berkeley Function-Calling Leaderboard / τ-bench — selection, parameters, sequencing, abstention</> },
  { m: "Reliability pass^k", w: "0.16",
    s: <>τ-bench — a case passes only if it succeeds on <em>all</em> k runs</> },
  { m: "Faithfulness", w: "0.15",
    s: <>FActScore / RAGAS atomic-claim groundedness</> },
  { m: "Harmful-refusal rate", w: "0.14",
    s: <>AgentHarm — declines harmful requests, doesn’t fire forbidden tools</> },
  { m: "Injection robustness", w: "0.14",
    s: <>AgentDojo / InjecAgent — 1 − probe failure rate</> },
  { m: "Calibration (ECE)", w: "0.08",
    s: <>Guo et al. 2017, with abstention</> },
  { m: "Cost & latency", w: "—",
    s: <>Measured per run — the price of the score</> },
];

/** The three deployment surfaces. */
const DEPLOY: { icon: ReactNode; h: string; p: string; tag: string }[] = [
  { icon: <IcoRail />, h: "In your CI", tag: "observe-only",
    p: "A GitHub Action runs the battery on every pull request and blocks the merge if your agent’s grade regresses. No production access, no runtime cost." },
  { icon: <IcoBus />, h: "On your bus", tag: "standards-native",
    p: "Speaks OpenTelemetry. Ingest traces from the frameworks and pipelines you already run — LangGraph, the OpenAI Agents SDK, or any OTel exporter." },
  { icon: <IcoShield />, h: "In your VPC", tag: "zero egress",
    p: "Self-hosted and air-gapped modes. A boot-time check refuses to start if any path would call out. A statement of what stays where, for your security team." },
];

/** Draw the metric trace once real data lands, as a progressive enhancement.
 *  Without JS the trace is already fully visible (CSS default). */
function useTraceDraw(ready: boolean) {
  const ref = useRef<SVGPathElement | null>(null);
  useEffect(() => {
    const tr = ref.current;
    if (!ready || !tr || typeof tr.getTotalLength !== "function") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const len = tr.getTotalLength();
    tr.style.strokeDasharray = String(len);
    tr.style.strokeDashoffset = String(len);
    // two rAFs so the initial (hidden) state paints before we animate to shown
    const id = requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        tr.style.transition = "stroke-dashoffset 1.1s var(--ease-escape)";
        tr.style.strokeDashoffset = "0";
      }));
    return () => cancelAnimationFrame(id);
  }, [ready]);
  return ref;
}

/** The hero instrument, fed by REAL data: the Safe Reference Assistant's latest
 *  valid certificate (the same endpoint that backs the public assistant seal —
 *  it never returns a placeholder). While loading, and when no certificate has
 *  been issued yet, the instrument states that honestly instead of showing a
 *  sample profile. */
function Instrument() {
  // undefined = loading · null = no valid certificate to feature
  const [live, setLive] = useState<LiveCert | null | undefined>(undefined);
  useEffect(() => {
    api.assistantCertification()
      .then((a: any) => {
        if (!a?.gradeable || !a.cert_id) { setLive(null); return; }
        return api.publicCertification(a.cert_id).then((c: any) => setLive({
          certId: a.cert_id,
          agentName: c.agent_name ?? a.agent_id,
          grade: c.grade ?? a.grade,
          index: indexFromCert(c),
          methodology: c.methodology_version || "current",
          points: toPoints(normalizeScores(c)),
        }));
      })
      .catch(() => setLive(null));
  }, []);

  const traceRef = useTraceDraw(!!live && live.points.length > 0);

  if (!live) {
    return (
      <div className="inst" aria-label={live === undefined
          ? "Loading the live safety report"
          : "No certified agent to feature yet"}>
        <div className="inst-top">
          <span>SAFETY REPORT</span>
          <span className="demo">{live === undefined ? "LOADING" : "AWAITING CERTIFICATION"}</span>
        </div>
        <div className="inst-body">
          <div className="grade-cell">
            <span className="lbl">Grade</span>
            <span className="g" style={{ color: "var(--faint)" }}>—</span>
            <span className="idx">Agenttic Index <b>—</b></span>
          </div>
          <div className="field">
            <div className="cap"><span>Metric profile</span><span>0 — 100</span></div>
            <svg className="trace-svg" viewBox="0 0 340 132" preserveAspectRatio="none" aria-hidden="true">
              <g stroke="var(--lp-grid)" strokeWidth="1">
                <line x1="0" y1="16" x2="340" y2="16" />
                <line x1="0" y1="49" x2="340" y2="49" />
                <line x1="0" y1="82" x2="340" y2="82" />
                <line x1="0" y1="115" x2="340" y2="115" />
              </g>
            </svg>
          </div>
        </div>
        <div className="inst-foot">
          <span>{live === undefined
            ? "fetching the live measurement…"
            : "this panel shows a real certificate — none is published yet"}</span>
          <Link className="sig" to="/scan">run a scan →</Link>
        </div>
      </div>
    );
  }

  const trace = live.points.map((m) => `${m.x},${m.y}`).join(" L");
  const area = `M${trace} L${live.points[live.points.length - 1].x},124 L${live.points[0].x},124 Z`;
  const gradeCol = gradeColor(live.grade);
  return (
    <div className="inst" role="img"
         aria-label={`Live safety report for ${live.agentName}: grade ${live.grade}`
           + (live.index != null ? `, Agenttic Index ${live.index}` : "")
           + `. Measured dimensions — `
           + live.points.map((m) => `${m.label} ${m.value}`).join(", ") + "."}>
      <div className="inst-top">
        <span>SAFETY REPORT · {live.agentName.toUpperCase()}</span>
        <span className="demo">LIVE · VERIFIED</span>
      </div>
      <div className="inst-body">
        <div className="grade-cell">
          <span className="lbl">Grade</span>
          <span className="g" style={{ color: gradeCol }}>{live.grade}</span>
          <span className="idx">Agenttic Index <b>{live.index ?? "—"}</b></span>
        </div>
        <div className="field">
          <div className="cap"><span>Metric profile</span><span>0 — 100</span></div>
          <svg className="trace-svg" viewBox="0 0 340 132" preserveAspectRatio="none" aria-hidden="true">
            <g stroke="var(--lp-grid)" strokeWidth="1">
              <line x1="0" y1="16" x2="340" y2="16" />
              <line x1="0" y1="49" x2="340" y2="49" />
              <line x1="0" y1="82" x2="340" y2="82" />
              <line x1="0" y1="115" x2="340" y2="115" />
            </g>
            <g stroke="var(--lp-hair)" strokeWidth="1">
              {live.points.map((m) => <line key={m.key} x1={m.x} y1="8" x2={m.x} y2="124" />)}
            </g>
            <path d={area} fill="var(--accent-soft)" />
            <path ref={traceRef} className="tr-draw" d={`M${trace}`} fill="none"
                  stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            <g fill="var(--panel)" stroke="var(--accent-hover)" strokeWidth="2">
              {live.points.map((m) => <circle key={m.key} cx={m.x} cy={m.y} r="3" />)}
            </g>
          </svg>
        </div>
      </div>
      <div className="legend">
        {live.points.map((m) => (
          <span key={m.key}><i>{m.label}</i> <b>{m.value}</b></span>
        ))}
      </div>
      <div className="inst-foot">
        <span>profile {live.methodology}</span>
        <Link className="sig" to={`/certified/${live.certId}`}>
          Ed25519 signed — verify →
        </Link>
      </div>
    </div>
  );
}

export function LandingPage() {
  return (
    <div className="lp">
      <SiteNav />

      {/* ---- HERO ------------------------------------------------------- */}
      <header className="lp-hero">
        <div className="wrap lp-hero__grid">
          <div className="lp-hero__copy">
            <Eyebrow>Agent academy · evaluate · assert · certify</Eyebrow>
            <div className="lp-hero__tag">Training playground for serious agents</div>
            <h1>The trust layer for agents.</h1>
            <p className="lp-hero__lede">
              A playground where teams put agents through realistic drills, see
              where their behaviour breaks, and earn evidence before the work
              reaches a customer.
            </p>
            <p className="lp-hero__lede">
              Agenttic records every run an agent takes, evaluates it against the
              tools it touched, and proves the behaviour still holds. Unit tests
              assert on values. We assert on <em>behaviour</em>.
            </p>
            <div className="lp-hero__cta">
              <Button href="/scan" variant="solid">Start a training drill</Button>
              <Button href="/engine" variant="ghost">Explore the playground</Button>
            </div>
            <p className="lp-hero__foot">
              Your traces, your environment, your evidence
            </p>
          </div>

          <div className="lp-hero__lab" aria-label="Sample agent training program">
            <div className="lp-lab__top">
              <span className="lp-lab__live"><i /> LIVE PLAYGROUND</span>
              <span>SUPPORT-01</span>
            </div>
            <div className="lp-lab__body">
              <div className="lp-lab__title">
                <span className="lp-orb" aria-hidden="true">✦</span>
                <div><b>Customer support agent</b><small>Release candidate · v4.2</small></div>
                <span className="lp-lab__ready">READY TO TRAIN</span>
              </div>
              <div className="lp-lab__route" aria-label="Three-stage training route">
                <div className="is-done"><span>01</span><b>Practice</b><small>24 drills</small></div>
                <div className="is-now"><span>02</span><b>Pressure test</b><small>8 scenarios</small></div>
                <div><span>03</span><b>Qualify</b><small>when evidence holds</small></div>
              </div>
              <div className="lp-lab__challenge">
                <span className="lp-challenge__number">07</span>
                <div><small>ACTIVE DRILL</small><b>Refund policy under pressure</b><p>Customer insists on an out-of-policy refund.</p></div>
                <span className="lp-pulse" aria-label="Drill in progress" />
              </div>
            </div>
            <div className="lp-lab__foot">Sample workspace · no customer data</div>
          </div>
        </div>
      </header>

      <section className="lp-program" aria-labelledby="program-title">
        <div className="wrap">
          <div className="lp-program__head">
            <Eyebrow>One place to improve and prove</Eyebrow>
            <h2 id="program-title">Train it. Test it. Certify it.</h2>
            <p>Turn agent reliability into a visible practice, not a launch-day hope.</p>
          </div>
          <div className="lp-program__grid">
            <article className="lp-program__card lp-program__card--train">
              <span className="lp-program__number">01</span>
              <span className="lp-program__icon">↗</span>
              <h3>Training camp</h3>
              <p>Repeat real tasks, grade every attempt, and keep the episodes that teach the agent what good looks like.</p>
              <Link to="/app/training-camp">Open training camp <span>→</span></Link>
            </article>
            <article className="lp-program__card">
              <span className="lp-program__number">02</span>
              <span className="lp-program__icon">⌁</span>
              <h3>Scenario lab</h3>
              <p>Put tools, policies, and customers under pressure in a controlled world that can actually change.</p>
              <Link to="/engine">Explore scenarios <span>→</span></Link>
            </article>
            <article className="lp-program__card lp-program__card--certify">
              <span className="lp-program__number">03</span>
              <span className="lp-program__icon">✦</span>
              <h3>Certification</h3>
              <p>Issue a credential only when the evidence is sufficient — or get the exact work still standing in the way.</p>
              <Link to="/certified">See certified agents <span>→</span></Link>
            </article>
          </div>
        </div>
      </section>

      {/* ---- THE GAP ------------------------------------------------------
          Why the old approach stops working. Everything after this is the
          answer to it, so it goes first. */}
      <section id="gap">
        <div className="wrap">
          <SectionHeading eyebrow={GAP.eyebrow} title={GAP.title} />
          <div className="lp-prose">
            {GAP.body.map((p) => <p key={p.slice(0, 24)}>{p}</p>)}
          </div>
        </div>
      </section>

      {/* ---- WHAT WE BUILT: the four-step spine -------------------------- */}
      <section id="built">
        <div className="wrap">
          <SectionHeading eyebrow="The evidence loop"
            title="Practice is how an agent earns a credential."
            sub={"One SDK call wraps an agent's runtime. Every turn, tool call "
              + "and result is captured as a signed trace. Agenttic then "
              + "evaluates that trace against sandboxed tools, so a regression "
              + "shows up as a step that changed — not as an output that feels "
              + "worse."} />
          <div className="lp-grid lp-grid--2">
            {SPINE.map((s) => (
              <div className="lp-cell" key={s.n}>
                <div className="lp-cell__k">{s.n} · {s.k}</div>
                <h3>{s.h}</h3>
                <p>{s.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- THE PRODUCT ------------------------------------------------- */}
      <section id="product">
        <div className="wrap">
          <SectionHeading eyebrow="Your coaching tape"
            title="A trace timeline an engineer can argue with."
            sub={"Every run opens as a step-by-step timeline: what the model "
              + "decided, which tool it reached for, what came back, and which "
              + "assertion held or broke. Failed steps name the assertion that "
              + "broke and the step it broke on."} />
          <div className="lp-statband">
            {PRODUCT_STATS.map((s) => (
              <div className="lp-statband__c" key={s.lab}>
                <div className="lp-statband__l">{s.lab}</div>
                <div className={"lp-statband__f" + (s.accent ? " is-accent" : "")}>
                  {s.fig}
                </div>
                <div className="lp-statband__s">{s.sub}</div>
              </div>
            ))}
          </div>
          <p className="lp-note">
            Sample data from one recorded run — not a customer figure.
          </p>
          {/* The supporting scorecard stays on the public route: the sample is
              visibly labelled and demonstrates the same criteria view used in
              the console. */}
          <div className="lp-product-scorecard">
            <ScorecardCard
              bar="support-agent v4.2 · sample data"
              metrics={SAMPLE_METRICS}
              rows={SAMPLE_ROWS}
            />
          </div>
          <div className="lp-verdict">
            <ProvenanceBadge scorer="code" />
            <ProvenanceBadge scorer="judge" calibrated alpha={0.87} />
            <ProvenanceBadge scorer="judge" calibrated={false} />
          </div>
        </div>
      </section>

      {/* ---- ON TOP OF WHAT YOU RUN ------------------------------------
          Kept through the repositioning on purpose. The mockup drops it, but
          "we do not replace your testing" is a truthful, differentiating claim,
          and dropping it would leave the page positioned AGAINST the incumbents
          it actually sits on top of. Pinned by landing.test.tsx. */}
      <section id="ontop">
        <div className="wrap">
          <SectionHeading eyebrow="Where we fit"
            title="We do not replace your testing. We build on it."
            sub={"Keep running LangSmith, deepeval, Future AGI, Braintrust, "
              + "Langfuse or your own scripts. We read the recordings they "
              + "already produce and answer the question none of them answer."} />
          <div className="lp-grid lp-grid--3">
            {ON_TOP.map((o) => (
              <div className="lp-cell" key={o.h}>
                <h3>{o.h}</h3>
                <p>{o.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- WHY NOW ----------------------------------------------------- */}
      {/* Inverted deliberately: this is the second dark beat in the scroll, so
          the six light sections after the program cards stop reading as one
          wall. Same inversion as `.lp-program`, not a new treatment. */}
      <section id="why" className="lp-invert">
        <div className="wrap">
          <SectionHeading eyebrow="Why now"
            title={"We cut the time and cost of shipping an agent, and hand you "
              + "the proof it behaved."} />
          <div className="lp-grid lp-grid--3">
            {WHY_NOW.map((w) => (
              <div className="lp-cell" key={w.k}>
                <div className="lp-cell__k">{w.k}</div>
                <p>{w.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- TRUST -------------------------------------------------------
          Two sentences kept through the repositioning because they are QUALIFIED
          claims, and the qualifier is the honest part. "No server in the
          evaluation loop" is true; "there is no server" is not — a hosted
          console, a public certificate page and billing all exist. Likewise a
          baseline that applies to any agent is not a suite fitted to every
          agent. Both are pinned by landing.test.tsx. */}
      <section id="trust">
        <div className="wrap">
          <SectionHeading eyebrow="Trust"
            title="Your agent and data never leave your machine."
            sub={"The harness, the checks and the trace capture run on your "
              + "hardware — there is no server in the evaluation loop. The "
              + "hosted console only holds what you choose to publish."} />
          <div className="lp-prose">
            <p>
              We start from a baseline that applies to any agent and go deeper
              where we have built depth. Any test that cannot tell a good agent
              from a bad one is thrown away.
            </p>
            <p className="hash">Ed25519 signed · public keys at /.well-known/agenttic-cert-keys.json</p>
          </div>
        </div>
      </section>

      {/* ---- FAQ + CTA --------------------------------------------------- */}
      <section id="faq">
        <div className="wrap lp-faq">
          <SectionHeading eyebrow="FAQ" title="The questions we get first." />
          {FAQ.map((f) => <FaqItem key={f.q} q={f.q}>{f.a}</FaqItem>)}
        </div>
      </section>

      <section id="setup">
        <div className="wrap lp-cta">
          <SectionHeading eyebrow="Start your first program"
            title="Give your agent a place to get better."
            sub={"Bring one agent you already believe is ready. Start with a drill, "
              + "then carry only earned evidence forward to certification."} />
          <div className="lp-hero__cta">
            <Button href="/scan" variant="solid">Open a live trace</Button>
            <Button href="/app/training-camp" variant="ghost">Enter training camp</Button>
          </div>
        </div>
      </section>

      <footer className="lp-foot">
        <div className="wrap">
          <span>© 2026 Agenttic · runs in your environment</span>
          <span>
            <Link to="/methodology">Methodology</Link> · <Link to="/status">Status</Link>
          </span>
        </div>
      </footer>
    </div>
  );
}

export default LandingPage;
