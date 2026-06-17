import { Link } from "react-router-dom";
import { ThemeToggle } from "../components/ThemeToggle";

const STEPS = [
  ["01", "Describe", "Tell us the agent's job and its rules. We turn those requirements into a benchmark of realistic and adversarial tests."],
  ["02", "Test", "Run your agent — your own API endpoint or a built-in one — against every case, capturing each tool call and decision."],
  ["03", "Check safety", "Does it refuse dangerous or destructive commands? Are its tool calls correct, in-bounds, and safe to execute?"],
  ["04", "Check correctness", "Does the final output actually match the stated requirements? Deterministic checks plus a calibrated judge."],
  ["05", "Keep watching", "Monitor the agent in production and get alerted the moment its safety or quality starts to drift."],
];

const FEATURES = [
  ["🛡️", "Catch unsafe actions", "Test whether your agent refuses destructive or dangerous commands instead of blindly executing them — before it does it for real.", "safety first"],
  ["🧰", "Verify tool-calling", "Check that the agent calls the right tools with the right arguments and stays inside the boundaries you set — no rogue calls.", "tool correctness"],
  ["✅", "Match the requirements", "Confirm the agent's output does what you actually asked — graded by deterministic checks and a calibrated LLM judge.", "correctness"],
  ["🎯", "Adversarial & red-team tests", "Every benchmark includes edge cases and adversarial / unsafe-request prompts that try to make the agent misbehave.", "red-team"],
  ["📊", "Clear scorecards", "See exactly what passed, what broke and why — with pass rates, cost, and latency — so you know if it's safe to ship.", "shareable"],
  ["📈", "Live drift monitoring", "Keep sampling production traffic and get alerted when safety or quality regresses against your tested baseline.", "production"],
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
          <ThemeToggle />
          <Link className="btn-primary" to="/signup">Get started</Link>
        </nav>
      </header>

      <main className="lp">
        <section className="hero">
          <span className="badge">Ship AI agents you can trust</span>
          <h1>Test your AI agents for safety<br /><span className="grad">before you ship them.</span></h1>
          <p className="sub">
            Agenttic puts your agent through realistic and adversarial tests:
            does it refuse dangerous or destructive commands, are its tool calls
            correct and safe, and does its output match what you asked for? You
            get a clear safety scorecard — before your users, or your
            infrastructure, find the answer the hard way.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Test your agent free</Link>
            <a className="btn-ghost" href="/api-docs">View the API</a>
          </div>
        </section>

        <section className="section">
          <h2>How it works</h2>
          <p className="lede">From your agent's requirements to a safety scorecard — and a live watch that keeps checking.</p>
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
          <h2>What we check</h2>
          <p className="lede">The three questions that decide whether an agent is safe to ship — plus the tools to keep it that way.</p>
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
          <h2>Make your agents safer</h2>
          <p className="lede" style={{ margin: "0 auto 24px" }}>
            Create a workspace and run your first safety scorecard in minutes —
            know what your agent does before your users do.
          </p>
          <div className="cta">
            <Link className="btn-primary" to="/signup">Test your agent free</Link>
            <Link className="btn-ghost" to="/login">Log in</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <span><span className="hex" style={{ color: "var(--accent)" }}>⬡</span> Agenttic</span>
          <a href="/api-docs">API docs</a>
          <Link to="/login">Log in</Link>
          <Link to="/signup">Sign up</Link>
          <span style={{ flex: 1 }} />
          <span>Safety testing for AI agents</span>
        </div>
      </footer>
    </>
  );
}
