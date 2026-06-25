import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ScanExperience } from "../components/ScanExperience";
import { Seal, SealMark } from "../components/Seal";
import { api } from "../api";

/** From pointing us at your agent to a published grade — the consumer arc. */
const STEPS = [
  ["01", "Point", "Paste your agent's API endpoint — or try the demo. Add an auth header if it needs one. No key required for your own endpoint."],
  ["02", "Scan", "We send a battery of safety probes — harmful requests, prompt injection, secret-leak and dangerous-tool traps — and watch how your agent responds."],
  ["03", "Grade", "Deterministic checks roll the answers into one A–F safety grade, with a plain-language breakdown of what it refused and where it's weak."],
  ["04", "Certify", "Mint a signed certificate pinned to that exact agent version — drop the badge on your site or README; anyone can verify it."],
];

/** The published agent-eval benchmarks whose methodology the grade implements. */
const ANCHORS = ["AgentHarm", "AgentDojo", "InjecAgent", "BFCL", "τ-bench"];

const FEATURES = [
  ["⛔", "Refuses harmful actions", "We ask your agent to do destructive and dangerous things. A safe agent declines instead of carrying them out.", "core"],
  ["🛡️", "Resists prompt injection", "We smuggle malicious instructions into the content your agent reads. A safe agent ignores them and stays on task.", "core"],
  ["🔒", "Keeps secrets safe", "We try to coax your agent into revealing credentials it's holding. A safe agent won't leak them.", "safety"],
  ["🧰", "Uses tools safely", "We tempt your agent to run dangerous actions. A safe agent refuses rather than firing off the risky call.", "safety"],
];

/** Pricing teaser — UI stub; payments wired later. */
const PLANS = [
  ["Free scan", "$0", "Scan your agent and see its A–F grade with the full breakdown.", "Scan now", false],
  ["Certified", "Soon", "Publish the signed badge, list in the directory, and re-scan on every release.", "Join the waitlist", true],
  ["Team", "Soon", "Continuous scanning in CI, multiple agents, and shared dashboards.", "Talk to us", false],
];

export function LandingPage() {
  // The assistant's REAL grade + cert (public, no auth). A seal renders only
  // when a verifiable certificate backs it — otherwise no letter (never a
  // placeholder). Self-activates once the cert is issued.
  const [asstCert, setAsstCert] = useState<{ grade?: string; cert_id?: string } | null>(null);
  useEffect(() => {
    let alive = true;
    api.assistantCertification()
      .then((c) => { if (alive && c?.grade && c?.cert_id) setAsstCert(c); })
      .catch(() => { /* no cert / offline → gradeless seal */ });
    return () => { alive = false; };
  }, []);
  return (
    <>
      <header>
        <nav className="lp-nav">
          <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
          <span className="spacer" />
          <Link className="navlink" to="/assistant">Safe assistant</Link>
          <Link className="navlink" to="/certified">Certified agents</Link>
          <Link className="navlink" to="/methodology">Methodology</Link>
          <a className="navlink" href="/api-docs">API docs</a>
          <Link className="navlink" to="/login">Log in</Link>
          <Link className="btn-primary" to="/scan">Scan my agent</Link>
        </nav>
      </header>

      <main className="lp">
        {/* The hero IS the scanner. */}
        <section className="scan-hero">
          <span className="badge">Agent Safety Certification</span>
          <h1>Is your AI agent<br /><span className="grad">safe to ship?</span></h1>
          <p className="sub">
            Point us at your agent and find out in minutes — does it refuse
            harmful requests, resist prompt injection, and keep secrets? You get a
            clear A–F safety grade you can trust and publish.
          </p>

          <ScanExperience />

          <div className="trust-strip">
            <Link to="/methodology" className="trust-lab" style={{ textDecoration: "none" }}>
              Grades anchored to published agent-safety benchmarks
            </Link>
            <div className="trust-row">
              {ANCHORS.map((a) => <span className="trust-chip" key={a}>{a}</span>)}
            </div>
          </div>
        </section>

        <section className="section">
          <h2>How it works</h2>
          <p className="lede">From pointing us at your agent to a grade you can publish — in minutes.</p>
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
          <p className="lede">Four safety checks decide the grade — in plain language, no jargon.</p>
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

        {/* Dogfood proof — the assistant we built on the same safety bar. The
            seal shows its grade ONLY when a real, verifiable certificate backs
            it (links to the public /certified page); otherwise no letter. */}
        <section className="section asst-promo">
          <div className="asst-promo-card">
            {asstCert ? (
              <Link to={`/certified/${asstCert.cert_id}`} title="View the public safety certificate">
                <Seal grade={asstCert.grade} size={120} />
              </Link>
            ) : (
              <Seal size={120} />
            )}
            <div className="asst-promo-body">
              <span className="badge">We use it too</span>
              <h2>Try our safe assistant</h2>
              <p>
                Meet the personal assistant we built on the same bar we grade you
                against — it shows every tool it uses and asks before doing
                anything sensitive, and it can't touch your files or secrets.
                {asstCert
                  ? <> It passed our own Safety Battery with a verified{" "}
                      <Link to={`/certified/${asstCert.cert_id}`}>Grade {asstCert.grade}</Link>.</>
                  : " Safe by construction, with an independent safety grade to come."}
              </p>
              <div className="cta">
                <Link className="btn-primary" to="/assistant">Try our safe assistant</Link>
                <Link className="btn-ghost" to="/methodology">How grading works</Link>
              </div>
            </div>
          </div>
        </section>

        <section className="section trust-section">
          <div className="trust-card">
            <SealMark />
            <h2>A grade people can verify</h2>
            <p>
              Every certificate is signed and pinned to a specific agent version,
              so a grade can't be faked or quietly outgrown. The badge links to a
              public page anyone can check — the grade, the per-check results, when
              it was issued, and whether it's still valid. See exactly how grading
              works in the <Link to="/methodology">methodology</Link>.
            </p>
            <div className="cta">
              <Link className="btn-ghost" to="/certified">See certified agents</Link>
              <Link className="btn-ghost" to="/methodology">How grading works</Link>
            </div>
          </div>
        </section>

        {/* Pricing teaser — UI stub, payments wired later. */}
        <section className="section">
          <h2>Pricing</h2>
          <p className="lede">Scanning is free. Publishing and continuous checks are coming soon.</p>
          <div className="price-grid">
            {PLANS.map(([name, price, blurb, cta, featured]) => (
              <div className={`price-card${featured ? " featured" : ""}`} key={name as string}>
                {featured ? <span className="price-flag">Most popular</span> : null}
                <div className="price-name">{name}</div>
                <div className="price-amt">{price}</div>
                <p className="price-blurb">{blurb}</p>
                <Link className={featured ? "btn-primary" : "btn-ghost"} to="/scan">{cta}</Link>
              </div>
            ))}
          </div>
          <p className="price-foot">No credit card to scan. We'll never charge without asking.</p>
        </section>

        <section className="section" style={{ textAlign: "center" }}>
          <h2>Scan your agent</h2>
          <p className="lede" style={{ margin: "0 auto 24px" }}>
            Know what your agent does before your users do — get its safety grade now.
          </p>
          <div className="cta" style={{ justifyContent: "center" }}>
            <Link className="btn-primary" to="/scan">Scan my agent</Link>
            <Link className="btn-ghost" to="/certified">Browse certified agents</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/scan">Scan my agent</Link>
          <Link to="/assistant">Safe assistant</Link>
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <a href="/api-docs">API docs</a>
          <Link to="/login">Log in</Link>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification — Tested with Agenttic</span>
        </div>
      </footer>
    </>
  );
}
