import { Link } from "react-router-dom";

const STEPS = [
  ["01", "Generate", "Turn a business doc into a versioned benchmark suite — LLM drafts, a human gate approves."],
  ["02", "Run", "Drive any agent (reference, black-box HTTP, or hosted Managed Agent) against the suite."],
  ["03", "Score", "Deterministic checks + a tiered LLM judge grade every run against the rubric."],
  ["04", "Report", "Client-ready scorecards with cost, latency, regressions, and judge rationale."],
  ["05", "Monitor", "Sample production traffic, detect drift vs the batch baseline, trigger re-evaluation."],
];

const FEATURES = [
  ["🧪", "A testbench for agents", "A UVM-style verification harness where the device under test is an AI agent: adapters drive it, the harness captures traces, the scoreboard grades them.", "glass-box & black-box"],
  ["🏆", "The Agenttic Index", "Rank agents across suites artificialanalysis-style — a weighted task-success Index with blended $/case, p95 latency, and honest coverage.", "leaderboard"],
  ["⚖️", "Tiered LLM judge", "A cheap executor model consults a stronger advisor only on borderline calls — calibrated against human labels, with provisional scores flagged.", "calibrated"],
  ["📈", "Live drift monitoring", "Sample live traffic, score on a light judge, compare rolling means to the batch baseline, and raise a re-eval request when quality slips.", "production"],
  ["💸", "Cost estimation & ceilings", "Project spend before a run, track real token cost (agent + judge), and abort cleanly at per-run / daily / per-tenant budget caps.", "$ guardrails"],
  ["🔒", "Multi-tenant & secured", "Per-tenant workspaces, viewer/operator/admin roles, token + session auth, rate limiting, and a versioned, migratable store.", "production-ready"],
];

export function LandingPage() {
  return (
    <>
      <header>
        <nav className="lp-nav">
          <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
          <span className="spacer" />
          <a className="navlink" href="/api-docs">API docs</a>
          <Link className="navlink" to="/login">Log in</Link>
          <Link className="btn-primary" to="/signup">Get started</Link>
        </nav>
      </header>

      <main className="lp">
        <section className="hero">
          <span className="badge">UVM-style testbench · for AI agents</span>
          <h1>Score and benchmark your agents.<br /><span className="grad">Know what breaks, and why.</span></h1>
          <p className="sub">
            Agenttic turns business requirements into versioned benchmark suites,
            runs any agent against them, and scores each run with deterministic
            checks plus a calibrated LLM judge — with a live path that catches
            production drift before your users do.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Start free</Link>
            <a className="btn-ghost" href="/api-docs">View the API</a>
          </div>
        </section>

        <section className="section">
          <h2>The evaluation loop</h2>
          <p className="lede">From a business doc to a client-ready scorecard — and back again as drift feeds new tests.</p>
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
          <h2>What you get</h2>
          <p className="lede">Everything to run agent evaluation as real engineering — not vibes.</p>
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

        <section className="section" style={{ textAlign: "center" }}>
          <h2>Ship agents you can trust</h2>
          <p className="lede" style={{ margin: "0 auto 24px" }}>
            Create a workspace, draft a suite, and run your first scorecard in minutes.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Create your workspace</Link>
            <Link className="btn-ghost" to="/login">Log in</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <span><span className="hex" style={{ color: "var(--cat-input)" }}>⬡</span> Agenttic</span>
          <a href="/api-docs">API docs</a>
          <Link to="/login">Log in</Link>
          <Link to="/signup">Sign up</Link>
          <span style={{ flex: 1 }} />
          <span>Agentic scoring &amp; benchmarking platform</span>
        </div>
      </footer>
    </>
  );
}
