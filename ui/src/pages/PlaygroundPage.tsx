import { Link } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import "./playground.css";

/* ============================================================================
   SPEC-5 Step 24 — the public playground hub. No signup, prerendered, synthetic.
   Three simulations that teach the methodology by letting you drive the real
   decision machinery. "The Gate" is the flagship; the others follow the same
   sim-core-is-the-machine contract.
   ========================================================================== */

interface SimCard {
  to: string | null;
  title: string;
  teaches: string;
  status: "live" | "soon";
}

const SIMS: SimCard[] = [
  {
    to: "/playground/gate", title: "The Gate", status: "live",
    teaches: "Why a change that lifts the average can still be rejected — and why that's the point.",
  },
  {
    to: null, title: "Drift Watch", status: "soon",
    teaches: "Why single bad answers don't page anyone, but a trend does.",
  },
  {
    to: null, title: "The Deferral", status: "soon",
    teaches: "Why knowing when to stop and ask a human is a scored skill.",
  },
];

export function PlaygroundPage() {
  return (
    <>
      <SiteNav />
      <main className="playground-page pg-hub" role="main">
        <header className="pg-header">
          <p className="pg-eyebrow">Playground · synthetic data</p>
          <h1>Hold the machinery in your hands</h1>
          <p className="pg-lede">
            Three simulations that run Agenttic’s real decision logic on
            synthetic data — no signup, nothing saved. The safest way to
            understand how we grade agents is to drive the graders yourself.
          </p>
        </header>

        <div className="pg-card-grid">
          {SIMS.map((s) => {
            const inner = (
              <>
                <span className="pg-card-title">
                  {s.title}
                  {s.status === "soon" && <span className="pg-card-soon">soon</span>}
                </span>
                <span className="pg-card-teaches">{s.teaches}</span>
              </>
            );
            return s.to ? (
              <Link key={s.title} to={s.to} className="pg-card is-live">{inner}</Link>
            ) : (
              <div key={s.title} className="pg-card is-soon" aria-disabled="true">{inner}</div>
            );
          })}
        </div>

        <p className="pg-synthetic-note">
          The data is synthetic; the logic is production, proven identical by an
          automated parity suite. See the <Link to="/methodology">methodology</Link>.
        </p>
      </main>
    </>
  );
}
