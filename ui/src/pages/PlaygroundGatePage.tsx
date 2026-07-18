import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SiteNav } from "../components/SiteNav";
import {
  BASELINE_MEANS, BASELINE_SUCCESS, CRITERIA, PRESETS, DEFAULT_STATE,
  runGateSim, buildGateInput, stateToParams, paramsToState,
  type GateSimState,
} from "../playground/gateSim";
import { pyFixedSigned } from "../sim-core";
import "./playground.css";

/* ============================================================================
   SPEC-5 Step 24 — "The Gate" (the flagship public playground sim).

   No signup, prerendered, synthetic-but-clearly-labelled data. Every verdict is
   produced by the REAL, parity-proven sim-core gate (see playground/gateSim.ts),
   so the receipt a prospect reads here is byte-for-byte what production emits.
   Keyboard-operable, shareable via URL params, reduced-motion safe.
   ========================================================================== */

const CRIT_LABEL: Record<string, string> = {
  tone: "Tone", accuracy: "Accuracy", safety: "Safety",
};

function Slider({ id, label, value, onChange }: {
  id: string; label: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <label className="pg-slider" htmlFor={id}>
      <span className="pg-slider-head">
        <span>{label}</span>
        <span className="pg-slider-val">{value.toFixed(2)}</span>
      </span>
      <input
        id={id} type="range" min={0} max={1} step={0.05} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
    </label>
  );
}

export function PlaygroundGatePage() {
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState<GateSimState>(() => paramsToState(params));

  // keep the URL in sync so any slider position is a shareable link
  useEffect(() => {
    setParams(stateToParams(state), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const result = useMemo(() => runGateSim(state), [state]);
  const input = useMemo(() => buildGateInput(state), [state]);

  const setCandidate = (cid: string, v: number) =>
    setState((s) => ({ ...s, candidate: { ...s.candidate, [cid]: v } }));

  const activePreset = PRESETS.find(
    (p) => JSON.stringify(p.state) === JSON.stringify(state),
  )?.id;

  return (
    <>
      <SiteNav />
      <main className="playground-page pg-gate" role="main">
        <header className="pg-header">
          <p className="pg-eyebrow">Playground · synthetic data</p>
          <h1>The Gate</h1>
          <p className="pg-lede">
            Before a change to an agent ships, it must beat the current version
            on the same tests — and it can’t quietly trade one virtue for
            another. Move the sliders and watch the promotion gate decide, live.
            This runs the <em>exact</em> logic that reviews real agents.
          </p>
        </header>

        <div className="pg-presets" role="group" aria-label="Instructive presets">
          {PRESETS.map((p) => (
            <button
              key={p.id} type="button"
              className={`pg-preset${activePreset === p.id ? " is-active" : ""}`}
              aria-pressed={activePreset === p.id}
              onClick={() => setState(p.state)}
            >
              <span className="pg-preset-label">{p.label}</span>
              <span className="pg-preset-blurb">{p.blurb}</span>
            </button>
          ))}
        </div>

        <div className="pg-lab">
          <section className="pg-controls" aria-label="Candidate controls">
            <h2 className="pg-h2">Candidate</h2>
            {CRITERIA.map((cid) => (
              <div key={cid} className="pg-crit-row">
                <Slider
                  id={`crit-${cid}`}
                  label={CRIT_LABEL[cid]}
                  value={state.candidate[cid]}
                  onChange={(v) => setCandidate(cid, v)}
                />
                <span className="pg-baseline-note">
                  baseline {BASELINE_MEANS[cid].toFixed(2)}
                </span>
              </div>
            ))}
            <div className="pg-crit-row">
              <Slider
                id="success-rate" label="Pass rate"
                value={state.successRateB}
                onChange={(v) => setState((s) => ({ ...s, successRateB: v }))}
              />
              <span className="pg-baseline-note">
                baseline {(BASELINE_SUCCESS * 100).toFixed(0)}%
              </span>
            </div>
            <label className="pg-check">
              <input
                type="checkbox" checked={state.dropSafety}
                onChange={(e) => setState((s) => ({ ...s, dropSafety: e.target.checked }))}
              />
              <span>Stop scoring <strong>safety</strong> (the “lobotomy”)</span>
            </label>
          </section>

          <section
            className={`pg-verdict ${result.promote ? "is-promote" : "is-reject"}`}
            aria-live="polite"
          >
            <div className="pg-verdict-badge">
              {result.promote ? "PROMOTE" : "REJECT"}
            </div>
            <p className="pg-receipt">{result.reason}</p>
            <div className="pg-deltas">
              {input.comparison.perCriterion.map((c) => (
                <span
                  key={c.criterionId}
                  className={`pg-delta ${c.delta < 0 ? "is-down" : c.delta > 0 ? "is-up" : ""}`}
                >
                  {CRIT_LABEL[c.criterionId] ?? c.criterionId} {pyFixedSigned(c.delta, 2)}
                </span>
              ))}
              {state.dropSafety && (
                <span className="pg-delta is-missing">Safety · unpaired</span>
              )}
            </div>
          </section>
        </div>

        <aside className="pg-cta">
          <p>This gate reviews real agents — not toys.</p>
          <Link className="btn-primary" to="/scan">See it on your agent →</Link>
          <Link className="pg-cta-alt" to="/playground">More simulations</Link>
        </aside>

        <p className="pg-synthetic-note">
          All numbers here are synthetic and for illustration. The decision logic
          is the production gate, proven identical by an automated parity suite.
        </p>
      </main>
    </>
  );
}
