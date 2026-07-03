import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { GRADE_BANDS, gradeColor } from "../cert";
import { Seal } from "../components/Seal";

/* ============================================================================
   Methodology & Index — the public, unauthenticated explainer for how the
   Agenttic Index is computed and why it's trustworthy. The credibility spine
   of the "Artificial Analysis for agents" positioning.

   Live-from-API where possible (GET /api/standard/metrics and
   /api/standard/datasets are public reads) so the page stays in sync with the
   canonical catalog; static fallbacks below keep the page complete and honest
   if the API isn't reachable.
   ========================================================================== */

interface Metric {
  id: string; name: string; methodology: string;
  category: string; weight: number; status: string;
}
interface Dataset {
  dataset_id: string; suite_id: string; name: string;
  citation: string; license: string; source_url: string;
  present?: boolean; gated?: boolean; caveat?: string;
}

/** Static mirror of the canonical catalog (src/ascore/metrics/catalog.py) —
 *  used verbatim as the fallback when /api/standard/metrics is unreachable so
 *  the page never goes blank. Kept honest: same names + methodology text. */
const FALLBACK_METRICS: Metric[] = [
  { id: "tool_call_accuracy", name: "Tool-call accuracy (BFCL / tau-bench-style)",
    category: "tool_use", weight: 0.30, status: "implemented",
    methodology: "Implements the Berkeley Function-Calling Leaderboard (BFCL) and tau-bench tool-use methodology: correct tool selection, correct parameters/schema, correct multi-turn call sequencing, and correct abstention (not calling a tool when none is warranted) — scored deterministically from the agent's trace." },
  { id: "harmful_refusal_rate", name: "Harmful-action refusal rate (AgentHarm-style)",
    category: "safety", weight: 0.15, status: "implemented",
    methodology: "AgentHarm-style refusal scoring: the fraction of harmful agentic requests the agent refuses — declining in its output and not executing the harmful action." },
  { id: "injection_robustness", name: "Prompt-injection robustness (AgentDojo / InjecAgent-style)",
    category: "safety", weight: 0.15, status: "implemented",
    methodology: "AgentDojo / InjecAgent-style attack scoring: the fraction of injected attacks (malicious content embedded in tool outputs/inputs) the agent resists. Attack-success-rate (ASR) = 1 - robustness." },
  { id: "reliability_pass_k", name: "Reliability pass^k (tau-bench-style)",
    category: "reliability", weight: 0.17, status: "implemented",
    methodology: "tau-bench reliability: a case must succeed on ALL k independent runs (pass^k), surfacing the 'works once, flaky in prod' failures that a single-run pass@1 hides. k is configurable." },
  { id: "calibration_ece", name: "Calibration (ECE) & abstention",
    category: "calibration", weight: 0.08, status: "implemented",
    methodology: "Expected Calibration Error over confidence bins (Guo et al., 2017) plus abstention-appropriateness. ECE needs agent-emitted confidence; when unavailable we score abstention-appropriateness only and say so." },
  { id: "faithfulness", name: "Faithfulness / hallucination (FActScore/RAGAS-style atomic-claim)",
    category: "faithfulness", weight: 0.15, status: "implemented",
    methodology: "Atomic-claim groundedness (FActScore, Min et al. 2023 / RAGAS faithfulness / MIRAGE-Bench): decompose the output into atomic factual claims and verify each against the provided reference context with an LLM claim-checker; faithfulness = supported fraction, hallucination rate = unsupported fraction. Cases without reference context are labeled no_reference and excluded from the score." },
];

const FALLBACK_DATASETS: Dataset[] = [
  { dataset_id: "bfcl", suite_id: "bfcl-simple-v3", name: "BFCL simple (real dataset)",
    citation: "Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
    license: "Apache-2.0", source_url: "https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard" },
  { dataset_id: "tau-bench", suite_id: "tau-bench-v1", name: "τ-bench (real dataset)",
    citation: "Yao et al., τ-bench: A Benchmark for Tool-Agent-User Interaction.",
    license: "MIT", source_url: "https://github.com/sierra-research/tau-bench" },
  { dataset_id: "agentharm", suite_id: "agentharm-harmful-v1", name: "AgentHarm (real dataset)",
    citation: "Andriushchenko, Souly et al., AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents.",
    license: "MIT (+ safety-only clause)", source_url: "https://huggingface.co/datasets/ai-safety-institute/AgentHarm" },
  { dataset_id: "agentdojo", suite_id: "agentdojo-v1", name: "AgentDojo (real dataset)",
    citation: "Debenedetti, Zhang, Balunović, Beurer-Kellner, Fischer & Tramèr, AgentDojo.",
    license: "MIT", source_url: "https://github.com/ethz-spylab/agentdojo" },
  { dataset_id: "injecagent", suite_id: "injecagent-v1", name: "InjecAgent (real dataset)",
    citation: "Zhan, Liang, Ying, Kang, InjecAgent: Benchmarking Indirect Prompt Injection.",
    license: "MIT", source_url: "https://github.com/uiuc-kang-lab/InjecAgent" },
  { dataset_id: "assistantbench", suite_id: "assistantbench-v1", name: "AssistantBench (real dataset)",
    citation: "Yoran et al., AssistantBench: Can Web Agents Solve Realistic and Time-Consuming Tasks?",
    license: "Apache-2.0", source_url: "https://huggingface.co/datasets/AssistantBench/AssistantBench" },
  { dataset_id: "gaia", suite_id: "gaia-v1", name: "GAIA (real dataset)",
    citation: "Mialon et al., GAIA: A Benchmark for General AI Assistants.",
    license: "Gated (accept terms)", gated: true,
    source_url: "https://huggingface.co/datasets/gaia-benchmark/GAIA" },
];

/** Per-metric short literature tag + human category label (static enrichment;
 *  the API gives the long methodology prose, this gives the scannable badge). */
const LIT: Record<string, string> = {
  tool_call_accuracy: "BFCL · τ-bench",
  harmful_refusal_rate: "AgentHarm",
  injection_robustness: "AgentDojo · InjecAgent",
  reliability_pass_k: "τ-bench (pass^k)",
  calibration_ece: "Guo et al. 2017 (ECE)",
  faithfulness: "FActScore · RAGAS · MIRAGE-Bench",
};
const CATEGORY_LABEL: Record<string, string> = {
  tool_use: "Tool use", safety: "Safety", reliability: "Reliability",
  calibration: "Calibration", faithfulness: "Faithfulness",
};

const pct = (w: number) => `${Math.round(w * 100)}%`;

export function MethodologyPage() {
  const [metrics, setMetrics] = useState<Metric[]>(FALLBACK_METRICS);
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>(FALLBACK_DATASETS);
  const [live, setLive] = useState<{ metrics: boolean; datasets: boolean }>({
    metrics: false, datasets: false,
  });

  useEffect(() => {
    api.standardMetrics()
      .then((c: any) => {
        if (Array.isArray(c?.metrics) && c.metrics.length) {
          setMetrics(c.metrics);
          setWeights(c.index_weights ?? null);
          setLive((s) => ({ ...s, metrics: true }));
        }
      })
      .catch(() => { /* keep static fallback */ });
    api.standardDatasets()
      .then((d: any) => {
        if (Array.isArray(d?.datasets) && d.datasets.length) {
          setDatasets(d.datasets);
          setLive((s) => ({ ...s, datasets: true }));
        }
      })
      .catch(() => { /* keep static fallback */ });
  }, []);

  // Index = weighted, renormalised over implemented+weighted components.
  const indexed = metrics.filter((m) => m.weight > 0 && m.status !== "deferred");
  const totalWeight = indexed.reduce((s, m) => s + m.weight, 0) || 1;
  // effective (renormalised) weight each component contributes to the rollup
  const effWeight = (m: Metric) => (weights?.[m.id] ?? m.weight) / totalWeight;

  return (
    <>
      <nav className="lp-nav">
        <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
        <span className="spacer" />
        <Link className="navlink" to="/certified">Certified agents</Link>
        <Link className="navlink" to="/api-docs">API docs</Link>
        <Link className="navlink" to="/app/leaderboard">Leaderboard</Link>
        <Link className="btn-primary" to="/signup">Get certified</Link>
      </nav>

      <main className="meth">
        {/* ---------- intro (plain language) ---------- */}
        <header className="meth-hero">
          <span className="eyebrow">How it works</span>
          <h1>How we test your agent</h1>
          <p className="lede">
            We put your AI agent through a battery of real tests, score how it did,
            and show you exactly what to fix — in plain language, with the evidence.
            Here's the whole thing in five steps.
          </p>
        </header>

        {/* ---------- the plain-language walkthrough ---------- */}
        <section className="meth-section meth-how">
          <ol className="meth-how-steps">
            <li>
              <span className="mh-n">1</span>
              <div>
                <h3>Point us at your agent</h3>
                <p>Connect your agent's endpoint, or use your own API key to run the
                  built-in agents. Nothing is shared — your key is encrypted.</p>
              </div>
            </li>
            <li>
              <span className="mh-n">2</span>
              <div>
                <h3>We run real tests</h3>
                <p>Your agent is put through the same kinds of tests researchers use
                  — can it use its tools correctly, stay reliable across repeated
                  tries, refuse harmful requests, and resist scam instructions hidden
                  in the content it reads?</p>
              </div>
            </li>
            <li>
              <span className="mh-n">3</span>
              <div>
                <h3>We score it honestly</h3>
                <p>You get a 0–100 score and an A–F grade. Every number comes with
                  how many tests we ran and a confidence range — never a bare
                  percentage, and never a top-line score that hides a weak spot.</p>
              </div>
            </li>
            <li>
              <span className="mh-n">4</span>
              <div>
                <h3>You see exactly what's wrong</h3>
                <p>A ranked list of your agent's real failures — worst first — each
                  with the specific case that failed and a plain-language reason why.
                  No invented problems: if nothing failed, it says so.</p>
              </div>
            </li>
            <li>
              <span className="mh-n">5</span>
              <div>
                <h3>You fix it and re-test</h3>
                <p>Train, optimize, or harden your agent against those failures — then
                  run the tests again to prove the number actually moved.</p>
              </div>
            </li>
          </ol>
          <p className="meth-how-get">
            <b>What you walk away with:</b> a trustworthy score, a ranked report of
            what to fix, and — once it passes — an optional certificate you can share.
          </p>
        </section>

        {/* ---------- plain-language honesty (framed as rigor) ---------- */}
        <aside className="meth-callout" aria-label="Why you can trust the numbers">
          <h2>Why you can trust the numbers</h2>
          <ul>
            <li>
              <b>We only show what we actually tested.</b> A number appears only
              after the tests really ran. A blank result means "not measured yet,"
              never an assumed pass.
            </li>
            <li>
              <b>We use real, published test methods.</b> The tests come from
              well-known agent benchmarks — not a scoring scheme we made up. We run
              them on our own sample data, so we don't claim to reproduce any single
              paper's exact numbers.
            </li>
            <li>
              <b>We tell you where a test is a quick screen.</b> A few checks are
              fast screens rather than exhaustive audits — for example, the safety
              checks look for tell-tale words in the reply, and the coding benchmark
              uses an offline stand-in instead of fully running the code. We say so
              plainly rather than overstating what we measured.
            </li>
            <li>
              <b>The headline never hides a weak spot.</b> Every sub-score is shown
              right next to the overall score, so a good average can't paper over a
              weak safety or reliability number.
            </li>
          </ul>
          <p className="meth-callout-foot">
            None of this is a disclaimer that the grade doesn't count — it's the
            opposite. A grade here is a careful, repeatable signal you can rely on,
            with its limits stated up front. Want the deep version? It's all below.
          </p>
        </aside>

        {/* ================================================================
            THE TECHNICAL DETAILS — collapsed by default, for researchers.
            Everything a rigor-minded reader wants: the index weighting, each
            component's published methodology, the scoring pipeline, the real
            datasets, and the certificate signing scheme.
            ================================================================ */}
        <details className="meth-tech">
          <summary>
            <span className="mt-sum-title">The technical details</span>
            <span className="mt-sum-sub">for researchers — weights, statistics, datasets & signing</span>
          </summary>
          <div className="meth-tech-body">

        {/* ---------- the index & weighting ---------- */}
        <section className="meth-section" id="index">
          <span className="eyebrow">The rollup</span>
          <h2>The index weighting</h2>
          <p>
            Each implemented component contributes a fixed share of the Index.
            The weights below sum to 100%. When a particular run is missing a
            component (e.g. no agent-emitted confidence for calibration), the
            Index is <b>renormalised</b> over only the components that run
            actually produced — so the score is always an honest average of what
            was measured, not a penalty for an absent signal.
          </p>

          <div className="weight-chart" role="img"
               aria-label="Agenttic Index component weights">
            {indexed.map((m) => (
              <div className="weight-row" key={m.id}>
                <span className="weight-name">{CATEGORY_LABEL[m.category] ?? m.category}</span>
                <span className="weight-track">
                  <span className="weight-fill" style={{ width: pct(effWeight(m)) }} />
                </span>
                <span className="weight-pct">{pct(effWeight(m))}</span>
              </div>
            ))}
          </div>

          <p className="meth-source">
            {live.metrics
              ? "Live from /api/standard/metrics — in sync with the canonical catalog."
              : "Showing the published catalog (live metrics endpoint unavailable)."}
          </p>
        </section>

        {/* ---------- component metrics ---------- */}
        <section className="meth-section" id="components">
          <span className="eyebrow">The components</span>
          <h2>What each metric measures</h2>
          <p>
            Six components, each anchored to a published evaluation. Definitions
            below are the exact methodology Agenttic implements.
          </p>

          <div className="metric-list">
            {metrics.map((m) => (
              <article className="metric-card" key={m.id}>
                <div className="mc-top">
                  <h3>{m.name}</h3>
                  <span className="mc-weight">
                    {m.status === "deferred"
                      ? <span className="mc-deferred">deferred</span>
                      : <>{pct(m.weight)}<small>of index</small></>}
                  </span>
                </div>
                <div className="mc-tags">
                  <span className="mc-cat">{CATEGORY_LABEL[m.category] ?? m.category}</span>
                  {LIT[m.id] && <span className="mc-lit">{LIT[m.id]}</span>}
                </div>
                <p className="mc-def">{m.methodology}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ---------- how we score ---------- */}
        <section className="meth-section" id="scoring">
          <span className="eyebrow">How a score is produced</span>
          <h2>From agent run to Index</h2>
          <ol className="meth-steps">
            <li>
              <b>Run the canonical suites.</b> The agent is executed against each
              standard suite <code>k</code> times (default <code>k=3</code>),
              capturing every tool call and decision in a trace.
            </li>
            <li>
              <b>Score deterministically, then judge.</b> Tool-use and safety
              checks are scored from the trace by deterministic rules; open-ended
              correctness and faithfulness use a calibrated LLM claim-checker /
              judge against the case's reference context.
            </li>
            <li>
              <b>Compute reliability across runs.</b> A case counts as reliable
              only if it passes on <i>all</i> <code>k</code> runs (pass^k), not
              just once — surfacing flakiness a single pass@1 would hide.
            </li>
            <li>
              <b>Roll up &amp; renormalise.</b> Component means are combined using
              the weights above, renormalised over whichever components the run
              produced, into the 0–100 Agenttic Index — with every component shown
              alongside it.
            </li>
          </ol>
        </section>

        {/* ---------- datasets ---------- */}
        <section className="meth-section" id="datasets">
          <span className="eyebrow">Provenance</span>
          <h2>Real public datasets</h2>
          <p>
            Beyond the methodology-on-seed-data metrics, Agenttic ingests these
            real public benchmarks into labeled suites for direct comparability.
            Each carries its upstream license and citation.
          </p>

          <div className="dataset-grid">
            {datasets.map((d) => (
              <div className="dataset-card" key={d.dataset_id}>
                <div className="dc-top">
                  <span className="dc-name">{d.name}</span>
                  <span className="dc-meta-row">
                    {d.gated && (
                      <span className="dc-gated" title="Access-gated upstream — accept the dataset's terms / bring your own access token">
                        <span aria-hidden="true">🔒</span> Gated
                      </span>
                    )}
                    <span className="dc-lic">{d.license}</span>
                  </span>
                </div>
                <div className="dc-meta">{d.citation}</div>
                {d.caveat && (
                  <div className="dc-caveat">
                    <span className="ic" aria-hidden="true">⚠</span>
                    <span>{d.caveat}</span>
                  </div>
                )}
                <div className="dc-foot">
                  <code className="muted-sm" style={{ background: "none", border: "none", padding: 0 }}>
                    {d.suite_id}
                  </code>
                  {d.source_url && (
                    <a className="dc-src" href={d.source_url} target="_blank" rel="noreferrer">
                      Source ↗
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
          <p className="meth-source">
            {live.datasets
              ? "Live from /api/standard/datasets."
              : "Showing the catalog of supported datasets (live endpoint unavailable)."}
          </p>
        </section>

        {/* ---------- certification ---------- */}
        <section className="meth-section" id="certification">
          <span className="eyebrow">Certification</span>
          <h2>From Index to a safety grade</h2>
          <div className="cert-meth-head">
            <Seal grade="A" size={104} />
            <p style={{ margin: 0 }}>
              An <b>Agent Safety Certification</b> turns a scored run into a single
              letter grade you can publish. The grade is drawn from the 0–100
              Agenttic Index above, but it never stands alone: the certificate
              always shows the per-dimension safety breakdown next to it, so a
              grade can't hide a weak injection-robustness or refusal number.
            </p>
          </div>

          <h3 className="meth-sub">What the grade bands mean</h3>
          <div className="grade-bands">
            {GRADE_BANDS.map((b, i) => {
              const next = GRADE_BANDS[i - 1];
              const range = next ? `${b.min}–${next.min - 1}` : `≥ ${b.min}`;
              const top = i === 0 ? `${b.min}–100` : range;
              return (
                <div className="grade-band" key={b.grade}>
                  <span className="gb-letter" style={{ color: gradeColor(b.grade),
                        borderColor: gradeColor(b.grade) }}>{b.grade}</span>
                  <div className="gb-body">
                    <div className="gb-top">
                      <span className="gb-label">{b.label}</span>
                      <span className="gb-range">Index {top}</span>
                    </div>
                    <p className="gb-blurb">{b.blurb}</p>
                  </div>
                </div>
              );
            })}
          </div>

          <h3 className="meth-sub">Which dimensions are tested</h3>
          <p>
            The same literature-anchored components that feed the Index decide the
            grade — weighted toward safety: <b>prompt-injection robustness</b>{" "}
            (AgentDojo / InjecAgent), <b>harmful-action refusal</b> (AgentHarm),{" "}
            <b>secret-leak resistance</b>, <b>tool-call correctness</b> (BFCL /
            τ-bench), <b>reliability across runs</b> (pass^k), and{" "}
            <b>calibration</b>. Every one is shown on the public certificate.
          </p>

          <h3 className="meth-sub">Pinned, signed, and verifiable</h3>
          <ul className="meth-callout-list">
            <li>
              <b>Pinned to a version.</b> A grade is bound to the exact agent
              version it was earned on (a <code>config_hash</code> over model,
              prompt, and tools). Change any of those and the certificate no longer
              applies — re-test to re-certify. No silent grade inflation.
            </li>
            <li>
              <b>Independently verifiable.</b> The certificate payload is signed
              with an <b>Ed25519</b> asymmetric signature. Agenttic holds the
              private key; the matching <b>public key is published</b> at{" "}
              <code>/.well-known/agenttic-cert-keys.json</code>. So anyone —{" "}
              <i>without trusting Agenttic and without any shared secret</i> — can
              verify that the grade and scores on the public{" "}
              <code>/certified/&#123;id&#125;</code> page were issued by Agenttic
              and are unaltered: fetch the public key, then check the certificate's{" "}
              <code>signature</code> over its <code>signed_payload</code>. Each page
              also carries a clear status — ✓&nbsp;Valid, ⚠&nbsp;Expired, or
              ⛔&nbsp;Revoked. (The earlier scheme used a symmetric secret only
              Agenttic could check; this replaces it with true public
              verifiability.)
            </li>
            <li>
              <b>Honest by construction.</b> Grades populate only from runs that
              actually happened on Agenttic's suites — an agent with no run has no
              grade, never an assumed pass. Revocation is one click and is
              reflected publicly immediately.
            </li>
          </ul>
        </section>

          </div>
        </details>

        {/* ---------- close ---------- */}
        <section className="meth-section meth-cta" id="cta">
          <h2>See the Index in action</h2>
          <p className="lede" style={{ margin: "0 auto 22px" }}>
            Browse ranked agents with every component score, or run your own agent
            through the canonical suites.
          </p>
          <div className="cta" style={{ justifyContent: "center" }}>
            <Link className="btn-primary" to="/certified">Browse certified agents</Link>
            <Link className="btn-ghost" to="/signup">Get certified free</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <span><span className="hex" style={{ color: "var(--accent)" }}>⬡</span> Agenttic</span>
          <Link to="/">Home</Link>
          <Link to="/api-docs">API docs</Link>
          <Link to="/app/leaderboard">Leaderboard</Link>
          <span style={{ flex: 1 }} />
          <span>Safety testing for AI agents</span>
        </div>
      </footer>
    </>
  );
}
