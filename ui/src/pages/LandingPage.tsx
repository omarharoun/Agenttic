import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { gradeColor } from "../cert";
import { HexMark, IcoRail, IcoBus, IcoShield } from "../components/Icons";

/* ============================================================================
   The public landing page — "the safety lab for AI agents".

   Design language: the instrument readout. Not a metric dashboard — one
   instrument with real hierarchy: the GRADE is the primary reading (engraved,
   huge); the seven metrics are a single calibrated trace on a ruled field.
   Ported into the Chronometer token system; all colour comes from CSS variables
   (see the .agx block in theme.css). The page is fully static — it prerenders to
   real HTML and works without JS. The metric trace draws in as a progressive
   enhancement when motion is allowed.
   ========================================================================== */

/** The seven metrics behind the Agenttic Index, in trace order. Each x/y is a
 *  point on the 0–340 × 0–132 ruled field (y inverted: lower y = higher score).
 *  Values are an illustrative reference profile, matching the hero aria-label. */
const METRICS: { key: string; label: string; value: number; x: number; y: number }[] = [
  { key: "tool-call", label: "tool-call", value: 93, x: 24, y: 32 },
  { key: "reliability", label: "reliability", value: 88, x: 72, y: 44 },
  { key: "faithful", label: "faithful", value: 90, x: 120, y: 37 },
  { key: "refusal", label: "refusal", value: 99, x: 168, y: 9 },
  { key: "injection", label: "injection", value: 95, x: 216, y: 21 },
  { key: "calibration", label: "calibration", value: 86, x: 264, y: 50 },
  { key: "cost", label: "cost", value: 78, x: 312, y: 68 },
];

const TRACE = METRICS.map((m) => `${m.x},${m.y}`).join(" L");
const AREA = `M${TRACE} L312,124 L24,124 Z`;
const LINE = `M${TRACE}`;

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
    code: "ascore certify -p cert-agent-safety-v1" },
  { pk: "03 · STAMP", h: "Grade & sign",
    p: "A grade, a coverage table, and a content-hashed, signed dossier — verifiable by anyone, tied to that agent version.",
    code: "ascore dossier verify ./dossier.json" },
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
    s: <>AgentDojo / InjecAgent — 1 − attack-success-rate</> },
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

/** Draw the metric trace on mount as a progressive enhancement. Without JS the
 *  trace is already fully visible (CSS default); this only animates it in. */
function useTraceDraw() {
  const ref = useRef<SVGPathElement | null>(null);
  useEffect(() => {
    const tr = ref.current;
    if (!tr || typeof tr.getTotalLength !== "function") return;
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
  }, []);
  return ref;
}

function Instrument() {
  const traceRef = useTraceDraw();
  const gradeCol = gradeColor("A");
  return (
    <div className="inst" role="img"
         aria-label="Safety report for agent “Orchestra”: grade A, Agenttic Index 91. Seven metrics plotted — tool-call 93, reliability 88, faithfulness 90, harmful-refusal 99, injection 95, calibration 86, cost 78.">
      <div className="inst-top">
        <span>SAFETY REPORT · AGENT “ORCHESTRA”</span>
        <span className="rec"><span className="live-dot" aria-hidden />MEASURED</span>
      </div>
      <div className="inst-body">
        <div className="grade-cell">
          <span className="lbl">Grade</span>
          <span className="g" style={{ color: gradeCol }}>A</span>
          <span className="idx">Agenttic Index <b>91</b></span>
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
              {METRICS.map((m) => <line key={m.key} x1={m.x} y1="8" x2={m.x} y2="124" />)}
            </g>
            <path d={AREA} fill="var(--accent-soft)" />
            <path ref={traceRef} className="tr-draw" d={LINE} fill="none"
                  stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            <g fill="var(--panel)" stroke="var(--accent-hover)" strokeWidth="2">
              {METRICS.map((m) => <circle key={m.key} cx={m.x} cy={m.y} r="3" />)}
            </g>
          </svg>
        </div>
      </div>
      <div className="legend">
        {METRICS.map((m) => (
          <span key={m.key}><i>{m.label}</i> <b>{m.value}</b></span>
        ))}
      </div>
      <div className="inst-foot">
        <span>profile cert-agent-safety-v1</span>
        <span className="sig" style={{ color: gradeCol }}>✓ signed · 3f5acc3c…</span>
      </div>
    </div>
  );
}

export function LandingPage() {
  return (
    <div className="agx">
      <nav>
        <div className="wrap">
          <Link to="/" className="brand"><HexMark /> Agenttic</Link>
          <div className="nl">
            <a href="#measure">What we measure</a>
            <a href="#how">How it works</a>
            <a href="#deploy">Deploy</a>
            <Link to="/methodology">Methodology</Link>
            <Link className="cta" to="/scan">Scan an agent</Link>
          </div>
        </div>
      </nav>

      {/* ===================== HERO ===================== */}
      <header className="hero">
        <div className="wrap">
          <div>
            <div className="eyebrow">The safety lab for AI agents</div>
            <h1>Measure what your agent <em>actually does.</em></h1>
            <p className="lede">
              Grade any agent against real, published safety benchmarks — not a
              vibe check. Get a signed, version-pinned certificate that says
              exactly what was tested, and exactly what wasn’t.
            </p>
            <div className="cta-row">
              <Link className="btn btn-g" to="/scan">Scan an agent</Link>
              <a className="btn btn-o" href="#measure">See what we measure</a>
            </div>
            <div className="hero-note">
              No API key to try it · runs in your CI or your VPC<br />
              nothing leaves your environment
            </div>
          </div>
          <Instrument />
        </div>
      </header>

      {/* provenance */}
      <section className="prov">
        <div className="wrap">
          <div className="l">Scored with published, peer-reviewed agent-eval methodologies</div>
          <div className="chips">
            {PROVENANCE.map((c) => <span className="chip" key={c}>{c}</span>)}
          </div>
        </div>
      </section>

      <div className="divide"><div className="r" /></div>

      {/* ===================== HOW ===================== */}
      <section className="blk" id="how">
        <div className="wrap">
          <div className="kick">The procedure</div>
          <h2>Point it at an agent. Get a graded, signed report.</h2>
          <p className="sub">
            A measurement sequence, not a dashboard. Every step leaves evidence
            you can inspect and re-run — each number traces back to individual
            test cases.
          </p>
          <div className="proc">
            {PROCEDURE.map((s) => (
              <div className="pstep" key={s.pk}>
                <span className="pn" />
                <div className="pk">{s.pk}</div>
                <h3>{s.h}</h3>
                <p>{s.p}</p>
                <code>{s.code}</code>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== MEASURE ===================== */}
      <section className="blk" id="measure">
        <div className="wrap">
          <div className="kick">The instrument</div>
          <h2>Seven metrics. One Agenttic Index.</h2>
          <p className="sub">
            Weighted, renormalized over whatever a run produces — every weight
            traceable to a published method. The Index is the headline; the seven
            are what it’s made of.
          </p>
          <div className="spec">
            {SPEC.map((r) => (
              <div className="srow" key={r.m}>
                <span className="m">{r.m}</span>
                <span className="s">{r.s}</span>
                <span className="w">{r.w}</span>
              </div>
            ))}
            <div className="snote">
              <b>Honest by construction.</b> Domains a profile doesn’t cover are
              stamped NOT ASSESSED, never guessed. A grade attests to what was
              measured — the coverage table always travels with it.
            </div>
          </div>
        </div>
      </section>

      {/* ===================== DEPLOY ===================== */}
      <section className="blk" id="deploy">
        <div className="wrap">
          <div className="kick">Where it runs</div>
          <h2>It comes to your environment.</h2>
          <p className="sub">Meet your agents where they already live — and keep the data where it already is.</p>
          <div className="c3">
            {DEPLOY.map((c) => (
              <div className="card" key={c.h}>
                {c.icon}
                <h3>{c.h}</h3>
                <p>{c.p}</p>
                <span className="t">{c.tag}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== CERTIFICATE ===================== */}
      <section className="blk" id="certificate">
        <div className="wrap">
          <div className="cert">
            <div className="st">Agent Safety Certification</div>
            <h2>A grade you can hand someone.</h2>
            <p>
              Every certificate is content-hashed and signed with a published key,
              pinned to the exact agent version tested. Change the model, prompt,
              or tools and it lapses — so a passing grade always means <em>this</em>{" "}
              agent, as measured.
            </p>
            <p className="hash">dossier fcdb6810 · Ed25519 · verify at /.well-known/agenttic-cert-keys.json</p>
          </div>
        </div>
      </section>

      {/* ===================== FINAL CTA ===================== */}
      <section className="blk final" id="scan">
        <div className="wrap">
          <div className="kick" style={{ textAlign: "center" }}>Run one now</div>
          <h2 style={{ maxWidth: "24ch" }}>Scan an agent in your next commit.</h2>
          <p className="sub" style={{ maxWidth: "52ch" }}>
            Start with the reference agent to see a full report, then point it at
            your own. Nothing leaves your machine.
          </p>
          <div className="cta-row" style={{ justifyContent: "center" }}>
            <Link className="btn btn-g" to="/scan">Scan an agent</Link>
            <Link className="btn btn-o" to="/methodology">Read the methodology</Link>
          </div>
        </div>
      </section>

      {/* footer */}
      <footer>
        <div className="wrap">
          <div className="fg">
            <div className="fb">Agenttic
              <span className="t">A safety lab for AI agents. We measure against published standards and stamp the result — honestly about what we did and didn’t test.</span>
            </div>
            <div className="fc">
              <div className="fcol"><h4>Product</h4>
                <a href="#measure">What we measure</a>
                <a href="#how">How it works</a>
                <a href="#deploy">Deploy</a>
                <Link to="/scan">Scan an agent</Link>
              </div>
              <div className="fcol"><h4>Developers</h4>
                <Link to="/api-docs">API docs</Link>
                <Link to="/methodology">OpenTelemetry ingest</Link>
                <Link to="/methodology">Self-hosting</Link>
                <Link to="/login">Log in</Link>
              </div>
              <div className="fcol"><h4>Trust</h4>
                <Link to="/methodology">Methodology</Link>
                <Link to="/methodology">Coverage &amp; limits</Link>
                <Link to="/certified">Verify a certificate</Link>
                <Link to="/methodology">Data residency</Link>
              </div>
            </div>
          </div>
          <div className="legal">
            Grades attest to what was tested under a named profile. Domains marked
            NOT ASSESSED are outside a profile’s current suites. Benchmark names are
            the property of their respective authors.
          </div>
        </div>
      </footer>
    </div>
  );
}
