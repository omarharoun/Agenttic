import { useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import { CertConversation } from "../components/CertConversation";
import { ScanExperience } from "../components/ScanExperience";
import { SealMark } from "../components/Seal";

/* ============================================================================
   /scan — the dedicated scanner page (also the primary public entry).

   Mobile-first. The hero IS the intake interview: Agenttic asks four quick
   questions, composes your certification profile beside the chat, then runs
   the scan in the same panel — one continuous surface from first question to
   stamped grade. The classic paste-a-URL form stays one click away for people
   who just want the instrument.
   ========================================================================== */

const HOW = [
  ["Point", "Paste your agent's API endpoint (or pick the demo). Add an auth header if it needs one."],
  ["Scan", "We send a battery of safety probes — harmful requests, prompt injection, secret-leak and dangerous-tool traps — and watch how your agent responds."],
  ["Grade", "Deterministic checks roll up into a single A–F safety grade, with a plain-language breakdown of what it refused and where it's weak."],
  ["Certify", "Mint a signed, shareable certificate pinned to the exact agent version we tested."],
];

export function ScanPage() {
  const [classic, setClassic] = useState(false);
  return (
    <>
      <SiteNav />

      <main className="lp scan-page">
        <section className="scan-hero">
          <span className="badge">Agent Safety Certification</span>
          <h1>Is your AI agent <span className="grad">safe to ship?</span></h1>
          <p className="sub">
            Four quick questions compose your certification profile — then the
            scan runs right on it. A clear safety grade in minutes.
          </p>
          {classic ? <ScanExperience /> : <CertConversation />}
          <button type="button" className="scan-link scan-mode-toggle"
                  onClick={() => setClassic((c) => !c)}>
            {classic ? "← Back to the guided interview" : "Prefer to just paste a URL? Use the quick form"}
          </button>
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
          <Link to="/api-docs">API docs</Link>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification — Tested with Agenttic</span>
        </div>
      </footer>
    </>
  );
}
