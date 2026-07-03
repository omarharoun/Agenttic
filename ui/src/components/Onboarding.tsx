import { useState } from "react";
import { Link } from "react-router-dom";

/* ============================================================================
   First-run guided onboarding — the "point → scan → grade → certify" arc.

   New users landed on a console with ~15 menu items and no thread connecting
   them. This dismissible strip lays out the whole journey in four steps and
   links each stage to the surface that does it (Scan, New evaluation / Compare,
   Training Camp / Optimize / Hardening, Certification), so the path is obvious
   instead of cold exploration. Dismissal persists in localStorage.
   ========================================================================== */

const DISMISS_KEY = "ascore_onboarding_dismissed";

interface Step {
  n: string;
  title: string;
  body: string;
  cta: { to: string; label: string };
  alt?: { to: string; label: string }[];
}

const STEPS: Step[] = [
  {
    n: "1", title: "Point & scan",
    body: "Point Agenttic at your agent's endpoint (or try the demo) and get a first A–F safety grade in minutes.",
    cta: { to: "/scan", label: "Scan an agent" },
  },
  {
    n: "2", title: "Evaluate deeper",
    body: "Run a full suite as a guided evaluation, or put two variants head-to-head to see which is safer.",
    cta: { to: "/app/build", label: "New evaluation" },
    alt: [{ to: "/app/compare", label: "Compare" }],
  },
  {
    n: "3", title: "Find & close gaps",
    body: "Train against a task, optimize the system prompt, and harden against the failures you surface.",
    cta: { to: "/app/training-camp", label: "Training Camp" },
    alt: [{ to: "/app/optimize", label: "Optimize" }, { to: "/app/hardening", label: "Harden" }],
  },
  {
    n: "4", title: "Certify & publish",
    body: "Mint a signed certificate pinned to the tested agent version, then publish the badge anyone can verify.",
    cta: { to: "/app/certifications", label: "Certification" },
  },
];

export function Onboarding() {
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1");
  if (dismissed) return null;
  const dismiss = () => { localStorage.setItem(DISMISS_KEY, "1"); setDismissed(true); };
  return (
    <section className="onboard" aria-label="Getting started">
      <button className="onboard-x" onClick={dismiss} title="Dismiss">✕</button>
      <div className="onboard-head">
        <span className="eyebrow">Getting started</span>
        <h2>From your agent to a published grade</h2>
        <p>Four steps connect the whole console — follow the thread instead of exploring the menu cold.</p>
      </div>
      <ol className="onboard-steps">
        {STEPS.map((s) => (
          <li className="onboard-step" key={s.n}>
            <div className="onboard-n">{s.n}</div>
            <h3>{s.title}</h3>
            <p>{s.body}</p>
            <div className="onboard-links">
              <Link className="btn-primary onboard-cta" to={s.cta.to}>{s.cta.label}</Link>
              {s.alt?.map((a) => (
                <Link className="onboard-alt" key={a.to} to={a.to}>{a.label}</Link>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <button className="onboard-dismiss" onClick={dismiss}>Got it — hide this</button>
    </section>
  );
}
