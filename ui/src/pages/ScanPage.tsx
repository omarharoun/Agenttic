import { useState } from "react";
import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import { CertConversation } from "../components/CertConversation";
import { ScanExperience } from "../components/ScanExperience";
import { SealMark } from "../components/Seal";
import "./ScanPage.css";

/* ============================================================================
   /scan — the dedicated scanner page (also the primary public entry).

   Mobile-first. The hero IS the intake interview: Agenttic asks four quick
   questions, composes your certification profile beside the chat, then runs
   the scan in the same panel — one continuous surface from first question to
   stamped grade. The classic paste-a-URL form stays one click away for people
   who just want the instrument.

   Framed as the first drill: the brief, the bay the drill runs in, then the
   four steps from endpoint to credential. Frame only — the interview, the
   classic form, the submit path and its 429 handling are untouched.
   ========================================================================== */

const HOW = [
  ["Point", "Paste your agent's API endpoint (or pick the demo). Add an auth header if it needs one."],
  ["Scan", "We send a battery of safety probes — harmful requests, prompt injection, secret-leak and dangerous-tool traps — and watch how your agent responds."],
  ["Grade", "Deterministic checks roll up into a single A–F safety grade, with a plain-language breakdown of what it refused and where it's weak."],
  ["Certify", "Mint a signed, shareable certificate pinned to the exact agent version we tested."],
];

/** The accent card is the drill itself; the last one closes the row. */
const STEP_TONE = ["", "sc-step--hot", "", "sc-step--seal"];

export function ScanPage() {
  const [classic, setClassic] = useState(false);
  return (
    <>
      <SiteNav />

      <main className="lp sc">
        <section className="sc-hero">
          <p className="sc-eyebrow">Agent academy · intake</p>
          <div className="sc-tag">Your first training drill</div>
          <h1>Is your AI agent <em>safe to ship?</em></h1>
          <p className="sc-lede">
            Four quick questions compose your certification profile — then the
            scan runs right on it. A clear safety grade in minutes.
          </p>

          <div className="sc-bay">
            <div className="sc-bay__rail">
              <b>Drill bay · intake</b>
              <span>{classic ? "quick form" : "guided interview"}</span>
            </div>
            {classic ? <ScanExperience /> : <CertConversation />}
          </div>

          <button type="button" className="scan-link sc-toggle"
                  onClick={() => setClassic((c) => !c)}>
            {classic ? "← Back to the guided interview" : "Prefer to just paste a URL? Use the quick form"}
          </button>
        </section>

        <section className="sc-program">
          <div className="sc-program__head">
            <p className="sc-eyebrow">The drill, end to end</p>
            <h2>Four steps from endpoint to credential.</h2>
            <p>Four steps, a couple of minutes, a grade you can publish.</p>
          </div>
          <div className="sc-steps">
            {HOW.map(([h, p], i) => (
              <article className={`sc-step ${STEP_TONE[i]}`.trim()} key={h}>
                <span className="sc-step__n">{String(i + 1).padStart(2, "0")}</span>
                <div className="sc-step__b">
                  <h3>{h}</h3>
                  <p>{p}</p>
                </div>
              </article>
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
