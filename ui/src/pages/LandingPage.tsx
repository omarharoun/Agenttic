import { Link } from "react-router-dom";
import { Seal, SealMark } from "../components/Seal";

/** How an agent goes from untested to certified — the certification arc. */
const STEPS = [
  ["01", "Describe", "Tell us the agent's job and its rules. We turn those requirements into a benchmark of realistic and adversarial safety tests."],
  ["02", "Test", "Run your agent against every case — injection attacks, harmful requests, tricky tool calls — capturing each decision in a trace."],
  ["03", "Grade", "Deterministic checks plus a calibrated judge roll the trace into a single safety grade, with every dimension shown."],
  ["04", "Certify", "Mint a signed certificate pinned to that exact agent version — A signed grade you can verify, not a claim."],
  ["05", "Publish", "Drop the badge on your site or README. It links to a public, verifiable certificate anyone can check."],
];

/** The published agent-eval benchmarks whose methodology the grade implements —
 *  shown as a trust strip under the hero CTA. */
const ANCHORS = [
  "BFCL", "τ-bench", "AgentHarm", "AgentDojo",
  "InjecAgent", "GAIA", "AssistantBench",
];

const FEATURES = [
  ["🛡️", "Injection robustness", "Tested against AgentDojo / InjecAgent-style attacks: does the agent resist malicious content smuggled into tool outputs and inputs?", "safety"],
  ["⛔", "Harmful-action refusal", "AgentHarm-style scoring: does the agent decline destructive or dangerous requests instead of executing them?", "safety"],
  ["🔒", "Secret-leak resistance", "Checks that the agent doesn't exfiltrate credentials, keys, or private context when probed or coerced.", "safety"],
  ["🧰", "Tool-call correctness", "BFCL / τ-bench methodology: right tool, right arguments, in-bounds — and abstaining when no tool is warranted.", "correctness"],
  ["✅", "Requirement match", "Confirms the agent's output does what was actually asked — deterministic checks plus a calibrated LLM judge.", "correctness"],
  ["📈", "Reliability across runs", "pass^k scoring surfaces the 'works once, flaky in prod' failures a single run hides — before they ship.", "reliability"],
];

export function LandingPage() {
  return (
    <>
      <header>
        <nav className="lp-nav">
          <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
          <span className="spacer" />
          <Link className="navlink" to="/certified">Certified agents</Link>
          <Link className="navlink" to="/methodology">Methodology</Link>
          <a className="navlink" href="/api-docs">API docs</a>
          <Link className="navlink" to="/login">Log in</Link>
          <Link className="btn-primary" to="/signup">Get certified</Link>
        </nav>
      </header>

      <main className="lp">
        <section className="hero">
          <span className="badge">Agent Safety Certification</span>
          <h1>Get your AI agent<br /><span className="grad">safety-certified.</span></h1>
          <p className="sub">
            Agenttic puts your agent through realistic and adversarial safety
            tests — does it resist prompt injection, refuse harmful actions, keep
            secrets, and call its tools correctly? — and turns the result into a
            signed, verifiable safety grade. <b>Tested with Agenttic</b> is a mark
            your users can trust.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Get your agent certified</Link>
            <Link className="btn-ghost" to="/certified">Browse certified agents</Link>
          </div>

          <div className="hero-seal">
            <Seal grade="A" size={120} />
          </div>

          <div className="trust-strip">
            <Link to="/methodology" className="trust-lab" style={{ textDecoration: "none" }}>
              Grades anchored to published agent benchmarks
            </Link>
            <div className="trust-row">
              {ANCHORS.map((a) => (
                <span className="trust-chip" key={a}>{a}</span>
              ))}
            </div>
          </div>
        </section>

        <section className="section">
          <h2>How certification works</h2>
          <p className="lede">From your agent's requirements to a signed grade you can publish — in minutes.</p>
          <div className="steps">
            {STEPS.map(([n, h, p]) => (
              <div className="step" key={n}>
                <div className="n">{n}</div>
                <h3>{h}</h3>
                <p>{p}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <h2>What the grade measures</h2>
          <p className="lede">Six literature-anchored dimensions decide the grade — safety first, with every component shown alongside the rollup.</p>
          <div className="features">
            {FEATURES.map(([ico, h, p, tag]) => (
              <div className="feat" key={h}>
                <div className="ico">{ico}</div>
                <h3>{h}</h3>
                <p>{p}</p>
                <span className="tag">{tag}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="section trust-section">
          <div className="trust-card">
            <SealMark />
            <h2>A mark people can verify</h2>
            <p>
              Every certificate is signed and pinned to a specific agent version,
              so a grade can't be faked or quietly outgrown. The badge links to a
              public page anyone can check — the grade, the per-dimension scores,
              when it was issued, and whether it's still valid. Read exactly how
              grading works in the <Link to="/methodology">methodology</Link>.
            </p>
            <div className="cta">
              <Link className="btn-ghost" to="/certified">See certified agents</Link>
              <Link className="btn-ghost" to="/methodology">How grading works</Link>
            </div>
          </div>
        </section>

        <section className="section" style={{ textAlign: "center" }}>
          <h2>Certify your agent</h2>
          <p className="lede" style={{ margin: "0 auto 24px" }}>
            Run your first safety scorecard and earn a grade you can publish —
            know what your agent does before your users do.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Get certified free</Link>
            <Link className="btn-ghost" to="/login">Log in</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <a href="/api-docs">API docs</a>
          <Link to="/login">Log in</Link>
          <Link to="/signup">Get certified</Link>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification — Tested with Agenttic</span>
        </div>
      </footer>
    </>
  );
}
