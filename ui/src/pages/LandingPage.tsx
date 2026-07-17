import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { gradeColor, indexFromCert, normalizeScores } from "../cert";
import { IcoRail, IcoBus, IcoShield } from "../components/Icons";
import { SiteNav } from "../components/SiteNav";

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
    <div className="agx">
      <SiteNav />

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
            {/* the funnel starts here: each chip answers the interview's first
                question, so /scan opens already one turn in. Plain links —
                prerenders as static HTML like the rest of the page. */}
            <div className="hero-intake">
              <span className="hi-lab">60-second intake — my agent…</span>
              <Link to="/scan?does=support">handles support</Link>
              <Link to="/scan?does=code">writes code</Link>
              <Link to="/scan?does=research">does research</Link>
              <Link to="/scan?does=ops">runs internal ops</Link>
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
            <p className="hash">Ed25519 signed · public keys at /.well-known/agenttic-cert-keys.json</p>
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
                <Link to="/pricing">Pricing</Link>
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
                <Link to="/status">System status</Link>
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
