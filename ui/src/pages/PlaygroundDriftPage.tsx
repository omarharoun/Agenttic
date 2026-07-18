import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  BASELINE, THRESHOLD, WINDOW, runDriftSim, windowFor,
  stateToParams, paramsToState, type DriftSimState,
} from "../playground/driftSim";
import "./playground.css";

/* SPEC-5 Step 24 — "Drift Watch". Real sim-core drift over a synthetic rolling
   window. Slider-driven (no timers), keyboard-operable, URL-shareable,
   reduced-motion safe (every value is text; the strip is static SVG). */

export function PlaygroundDriftPage() {
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState<DriftSimState>(() => paramsToState(params));

  useEffect(() => {
    setParams(stateToParams(state), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const result = useMemo(() => runDriftSim(state), [state]);
  const scores = useMemo(() => windowFor(state), [state]);

  // strip geometry
  const W = 480, H = 120, pad = 8;
  const dx = (W - pad * 2) / (WINDOW - 1);
  const y = (v: number) => H - pad - v * (H - pad * 2);
  const meanY = y(result.mean);
  const baseY = y(BASELINE);
  const floorY = y(BASELINE - THRESHOLD);

  return (
    <>
      <SiteNav />
      <main className="playground-page pg-drift" role="main">
        <header className="pg-header">
          <p className="pg-eyebrow">Playground · synthetic data</p>
          <h1>Drift Watch</h1>
          <p className="pg-lede">
            A live agent’s quality wanders. One bad answer isn’t a fire — a
            <em> trend</em> is. Degrade the stream and watch the rolling window
            absorb noise until the mean falls far enough below the batch baseline
            to fire a re-evaluation. This is the exact drift rule production runs.
          </p>
        </header>

        <div className="pg-lab pg-lab-drift">
          <section className="pg-controls" aria-label="Stream controls">
            <label className="pg-slider" htmlFor="degradation">
              <span className="pg-slider-head">
                <span>Degradation</span>
                <span className="pg-slider-val">{(state.degradation * 100).toFixed(0)}%</span>
              </span>
              <input
                id="degradation" type="range" min={0} max={1} step={0.05}
                value={state.degradation}
                onChange={(e) => setState({ degradation: parseFloat(e.target.value) })}
              />
            </label>
            <dl className="pg-facts">
              <div><dt>Window mean</dt><dd>{result.mean.toFixed(2)}</dd></div>
              <div><dt>Batch baseline</dt><dd>{BASELINE.toFixed(2)}</dd></div>
              <div><dt>Fire threshold</dt><dd>drop &gt; {THRESHOLD.toFixed(2)}</dd></div>
            </dl>
          </section>

          <section className={`pg-verdict ${result.fired ? "is-reject" : "is-promote"}`} aria-live="polite">
            <svg viewBox={`0 0 ${W} ${H}`} className="pg-strip" role="img"
                 aria-label={`Rolling window mean ${result.mean.toFixed(2)} against baseline ${BASELINE.toFixed(2)}`}>
              <rect x={pad} y={baseY} width={W - pad * 2} height={floorY - baseY}
                    className="pg-strip-band" />
              <line x1={pad} x2={W - pad} y1={floorY} y2={floorY} className="pg-strip-threshold" />
              <line x1={pad} x2={W - pad} y1={meanY} y2={meanY}
                    className={`pg-strip-mean ${result.fired ? "is-fired" : ""}`} />
              {scores.map((v, i) => (
                <circle key={i} cx={pad + i * dx} cy={y(v)} r={3}
                        className={v >= 0.5 ? "pg-dot-ok" : "pg-dot-bad"} />
              ))}
            </svg>
            <div className="pg-verdict-badge">{result.fired ? "RE-EVAL FIRED" : "STABLE"}</div>
            {result.reason && <p className="pg-receipt">{result.reason}</p>}
            {!result.reason && (
              <p className="pg-receipt pg-muted">
                Within tolerance — a dip this small doesn’t page anyone.
              </p>
            )}
          </section>
        </div>

        <aside className="pg-cta">
          <p>Agenttic watches your live agents for exactly this.</p>
          <Link className="btn-primary" to="/scan">Grade your agent →</Link>
          <Link className="pg-cta-alt" to="/playground">More simulations</Link>
        </aside>
        <p className="pg-synthetic-note">
          Synthetic stream; the drift decision is the production rule, proven
          identical by an automated parity suite.
        </p>
      </main>
    </>
  );
}
