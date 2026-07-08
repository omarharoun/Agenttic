import { Link } from "react-router-dom";
import { HexMark } from "../components/Icons";
import { ScanExperience } from "../components/ScanExperience";
import { SealMark } from "../components/Seal";

/* ============================================================================
   /scan — the dedicated scanner page (also the primary public entry).

   Mobile-first. The hero IS the scanner: one input, one action, a live scan that
   culminates in a graded seal. Everything below is quiet and explanatory.
   ========================================================================== */

const HOW = [
  ["Point", "Paste your agent's API endpoint (or pick the demo). Add an auth header if it needs one."],
  ["Scan", "We send a battery of safety probes — harmful requests, prompt injection, secret-leak and dangerous-tool traps — and watch how your agent responds."],
  ["Grade", "Deterministic checks roll up into a single A–F safety grade, with a plain-language breakdown of what it refused and where it's weak."],
  ["Certify", "Mint a signed, shareable certificate pinned to the exact agent version we tested."],
];

export function ScanPage() {
  return (
    <>
      <header>
        <nav className="lp-nav">
          <Link to="/" className="brand"><HexMark className="hex" /> Agenttic</Link>
          <span className="spacer" />
          <Link className="navlink" to="/certified">Certified agents</Link>
          <Link className="navlink" to="/methodology">Methodology</Link>
          <Link className="navlink" to="/login">Log in</Link>
        </nav>
      </header>

      <main className="lp scan-page">
        <section className="scan-hero">
          <span className="badge">Agent Safety Certification</span>
          <h1>Is your AI agent <span className="grad">safe to ship?</span></h1>
          <p className="sub">
            Point us at your agent and get a clear safety grade in minutes — does
            it refuse harmful requests, resist prompt injection, and keep secrets?
          </p>
          <ScanExperience />
        </section>

        <section className="section">
          <h2>How it works</h2>
          <p className="lede">Four steps, a couple of minutes, a grade you can publish.</p>
          <div className="steps">
            {HOW.map(([h, p], i) => (
              <div className="step" key={h}>
                <div className="n">{String(i + 1).padStart(2, "0")}</div>
                <h3>{h}</h3>
                <p>{p}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <a href="/api-docs">API docs</a>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification — Tested with Agenttic</span>
        </div>
      </footer>
    </>
  );
}
