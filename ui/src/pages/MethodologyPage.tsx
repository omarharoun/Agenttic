import { useEffect, useState } from "react";
import { HexMark } from "../components/Icons";
import { SiteNav } from "../components/SiteNav";
import { Link } from "react-router-dom";
import { Button, Eyebrow, SectionHeading } from "../components/ds";
import { api } from "../api";
import { GRADE_BANDS, gradeColor } from "../cert";
import { Seal } from "../components/Seal";
import "../landing/landing.css";
import "./MethodologyPage.css";

/* ============================================================================
   Methodology — the public, unauthenticated explainer for how an agent is put
   through the programme and what the resulting mark means.

   Restyled onto the landing's academy language (landing.css is the contract):
   serif headlines, mono eyebrows, a numbered progression, one inverted dark
   section for rhythm. Structure classes come from `.lp`; only the
   methodology-specific surfaces live in MethodologyPage.css. Tokens only.

   Live-from-API where possible (GET /api/standard/metrics and
   /api/standard/datasets are public reads) so the page stays in sync with the
   canonical catalog; static fallbacks below keep the page complete and honest
   if the API isn't reachable. Every figure here is a published catalog weight
   or a grade band — none of it is a customer metric.
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

/** Static mirror of the canonical catalog (src/agenttic/metrics/catalog.py) —
 *  used verbatim as the fallback when /api/standard/metrics is unreachable so
 *  the page never goes blank. Kept honest: same names + methodology text. */
const FALLBACK_METRICS: Metric[] = [
  { id: "tool_call_accuracy", name: "Tool-call accuracy (BFCL / tau-bench-style)",
    category: "tool_use", weight: 0.28, status: "implemented",
    methodology: "Implements the Berkeley Function-Calling Leaderboard (BFCL) and tau-bench tool-use methodology: correct tool selection, correct parameters/schema, correct multi-turn call sequencing, and correct abstention (not calling a tool when none is warranted) — scored deterministically from the agent's trace." },
  { id: "harmful_refusal_rate", name: "Harmful-action refusal rate (AgentHarm-style)",
    category: "safety", weight: 0.14, status: "implemented",
    methodology: "AgentHarm-style refusal scoring: the fraction of harmful agentic requests the agent refuses — declining in its output and not executing the harmful action." },
  { id: "injection_robustness", name: "Prompt-injection robustness (AgentDojo / InjecAgent-style)",
    category: "safety", weight: 0.14, status: "implemented",
    methodology: "AgentDojo / InjecAgent-style attack scoring: the fraction of injected attacks (malicious content embedded in tool outputs/inputs) the agent resists. Attack-success-rate (ASR) = 1 - robustness." },
  { id: "reliability_pass_k", name: "Reliability pass^k (tau-bench-style)",
    category: "reliability", weight: 0.16, status: "implemented",
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
/** Bar labels for the weighting chart. Two components share the `safety`
 *  category, so labelling the bars by category printed "Safety" twice against
 *  different weights — read as a typo, or worse, as double-counting. */
const SHORT_LABEL: Record<string, string> = {
  tool_call_accuracy: "Tool use",
  harmful_refusal_rate: "Harmful refusal",
  injection_robustness: "Injection robustness",
  reliability_pass_k: "Reliability",
  calibration_ece: "Calibration",
  faithfulness: "Faithfulness",
};

/** The programme, in the order an agent walks it. */
const STAGES = [
  { n: "01", k: "Enrol",
    h: "Point us at your agent",
    p: "Connect your agent's endpoint, or bring your own API key to run the built-in agents. Nothing is shared — your key is encrypted." },
  { n: "02", k: "Practice",
    h: "It works the drills",
    p: "Your agent runs drills built from the same evaluations researchers publish — can it use its tools correctly, hold up across repeated attempts, refuse harmful requests, and resist instructions smuggled into the content it reads?" },
  { n: "03", k: "Pressure-test",
    h: "The marking is honest",
    p: "You get a 0–100 score and an A–F grade. Every number arrives with how many drills ran and a confidence range — never a bare percentage, and never a top-line score that hides a weak spot." },
  { n: "04", k: "Coaching tape",
    h: "You see exactly what broke",
    p: "A ranked list of your agent's real failures — worst first — each with the drill that failed and a plain-language reason why. No invented problems: if nothing failed, it says so." },
  { n: "05", k: "Requalify",
    h: "You fix it and run it again",
    p: "Train, optimise, or harden your agent against those failures — then re-run the drills to prove the number actually moved." },
];

/** The marking rules. Each one is a constraint on what we are allowed to show,
 *  not a feature — which is why they read as rules rather than benefits. */
const MARKS = [
  { n: "01", h: "Only what was actually run",
    p: "A number appears only after the drills really ran. A blank result means “not measured yet” — never an assumed pass." },
  { n: "02", h: "Published methods, not ours",
    p: "The drills come from well-known agent benchmarks, not a scoring scheme we made up. We run them on our own sample data, so we don't claim to reproduce any single paper's exact numbers." },
  { n: "03", h: "A quick screen says it is one",
    p: "A few checks are fast screens rather than exhaustive audits — the safety checks look for tell-tale wording in the reply, and the coding benchmark uses an offline stand-in instead of fully running the code. We say so plainly rather than overstating what we measured." },
  { n: "04", h: "The headline can't hide a weak spot",
    p: "Every sub-score sits right next to the overall score, so a good average can't paper over a weak safety or reliability number." },
];

const OUTLINE = [
  { n: "01", href: "#programme", h: "The programme", s: "five stages" },
  { n: "02", href: "#marking", h: "What earns a mark", s: "the marking rules" },
  { n: "03", href: "#manual", h: "The coaching manual", s: "weights · datasets · signing" },
];

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
    <div className="lp mth">
      <SiteNav />

      {/* One <main>, and the hero sits inside it: a top-level <header> is a
          second banner landmark beside the nav, which is an axe violation. */}
      <main>

      {/* ---- HERO -------------------------------------------------------- */}
      <header className="lp-hero mth-hero">
        <div className="wrap mth-hero__grid">
          <div className="lp-hero__copy">
            <Eyebrow>Agent academy · the training method</Eyebrow>
            <div className="lp-hero__tag">Practice · pressure-test · qualify</div>
            <h1>How an agent earns its credential.</h1>
            <p className="lp-hero__lede">
              We put your agent through drills drawn from published agent
              benchmarks, mark what it actually did, and hand back the tape —
              the failures, worst first, with the evidence attached.
            </p>
            <p className="lp-hero__lede">
              Nothing on a scorecard here is a claim about your agent until a
              run produced it. This page is the whole method, in order.
            </p>
            <p className="lp-hero__foot">
              Your traces · your environment · your evidence
            </p>
          </div>

          <nav className="mth-outline" aria-label="On this page">
            <div className="mth-outline__bar">
              <span>COURSE OUTLINE</span>
              <span>/METHODOLOGY</span>
            </div>
            <ol className="mth-outline__list">
              {OUTLINE.map((o) => (
                <li key={o.href}>
                  <a href={o.href}>
                    <i>{o.n}</i>
                    <b>{o.h}</b>
                    <small>{o.s}</small>
                    <span aria-hidden="true">→</span>
                  </a>
                </li>
              ))}
            </ol>
            <p className="mth-outline__foot">
              The manual holds the deep version: weighting, statistics,
              datasets and the signing scheme.
            </p>
          </nav>
        </div>
      </header>

      {/* ---- THE PROGRAMME ----------------------------------------------- */}
      <section id="programme">
        <div className="wrap">
          <SectionHeading eyebrow="The programme"
            title="Five stages, one credential."
            sub={"An agent walks the same route every time: enrol, practice, "
              + "pressure-test, read the tape, requalify. Each stage produces "
              + "evidence the next one is allowed to use."} />
          <ol className="mth-stages">
            {STAGES.map((s) => (
              <li className="mth-stage" key={s.n}>
                <span className="mth-stage__n">{s.n}</span>
                <div className="mth-stage__body">
                  <span className="mth-stage__k">{s.k}</span>
                  <h3>{s.h}</h3>
                  <p>{s.p}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mth-take">
            <b>What you walk away with</b> — a trustworthy score, a ranked
            report of what to fix, and, once it qualifies, an optional
            credential you can publish.
          </p>
        </div>
      </section>

      {/* ---- WHAT EARNS A MARK (inverted band) --------------------------- */}
      <section className="mth-invert" id="marking" aria-labelledby="marking-title">
        <div className="wrap">
          <div className="mth-invert__head">
            <Eyebrow>The marking rules</Eyebrow>
            <h2 id="marking-title">What earns a mark here.</h2>
            <p>Four constraints on what we are allowed to put on a scorecard.</p>
          </div>
          <div className="mth-marks">
            {MARKS.map((m) => (
              <article className="mth-mark" key={m.n}>
                <span className="mth-mark__n">{m.n}</span>
                <h3>{m.h}</h3>
                <p>{m.p}</p>
              </article>
            ))}
          </div>
          <p className="mth-invert__foot">
            None of this is a disclaimer that the grade doesn't count — it is
            the opposite. A mark here is a careful, repeatable signal you can
            rely on, with its limits stated up front.
          </p>
        </div>
      </section>

      {/* ================================================================
          THE COACHING MANUAL — collapsed by default, for researchers.
          Everything a rigor-minded reader wants: the index weighting, each
          component's published methodology, the scoring pipeline, the real
          datasets, and the certificate signing scheme.
          ================================================================ */}
      <section id="manual">
        <div className="wrap">
          <details className="mth-manual">
            <summary className="mth-manual__sum">
              <span className="mth-manual__k">03 · For researchers</span>
              <span className="mth-manual__t">The coaching manual</span>
              <span className="mth-manual__s">
                Weighting, statistics, datasets and the signing scheme
              </span>
            </summary>
            <div className="mth-manual__body">

              {/* ---- the index & weighting ---- */}
              <div className="mth-block" id="index">
                <SectionHeading eyebrow="The rollup"
                  title="How the drills add up."
                  sub={"Each implemented component contributes a fixed share of "
                    + "the Index. When a run is missing a component (no "
                    + "agent-emitted confidence for calibration, say), the Index "
                    + "is renormalised over only the components that run actually "
                    + "produced — an honest average of what was measured, not a "
                    + "penalty for an absent signal."} />
                <div className="mth-w" role="img"
                     aria-label="Agenttic Index component weights">
                  {indexed.map((m) => (
                    <div className="mth-w__row" key={m.id}>
                      <span className="mth-w__name">
                        {SHORT_LABEL[m.id] ?? CATEGORY_LABEL[m.category] ?? m.category}
                      </span>
                      <span className="mth-w__track">
                        <span className="mth-w__fill" style={{ width: pct(effWeight(m)) }} />
                      </span>
                      <span className="mth-w__pct">{pct(effWeight(m))}</span>
                    </div>
                  ))}
                </div>
                <p className="mth-source">
                  {live.metrics
                    ? "Live from /api/standard/metrics — in sync with the canonical catalog."
                    : "Showing the published catalog (live metrics endpoint unavailable)."}
                </p>
              </div>

              {/* ---- component metrics ---- */}
              <div className="mth-block" id="components">
                <SectionHeading eyebrow="The drills"
                  title="What each component measures."
                  sub={"Each one is anchored to a published evaluation. The "
                    + "definitions below are the exact methodology Agenttic "
                    + "implements."} />
                <div className="lp-grid lp-grid--2">
                  {metrics.map((m) => (
                    <article className="lp-cell mth-metric" key={m.id}>
                      <div className="mth-metric__top">
                        <h3>{m.name}</h3>
                        <span className="mth-metric__w">
                          {m.status === "deferred"
                            ? <span className="mth-metric__def">deferred</span>
                            : <>{pct(m.weight)}<small>of index</small></>}
                        </span>
                      </div>
                      <div className="mth-tags">
                        <span className="mth-tag">
                          {CATEGORY_LABEL[m.category] ?? m.category}
                        </span>
                        {LIT[m.id] && <span className="mth-tag is-lit">{LIT[m.id]}</span>}
                      </div>
                      <p>{m.methodology}</p>
                    </article>
                  ))}
                </div>
              </div>

              {/* ---- how we score ---- */}
              <div className="mth-block" id="scoring">
                <SectionHeading eyebrow="From run to index"
                  title="How a mark is produced." />
                <ol className="mth-pipe">
                  <li>
                    <b>Run the canonical suites.</b> The agent is executed against
                    each standard suite <code>k</code> times (default{" "}
                    <code>k=3</code>), capturing every tool call and decision in a
                    trace.
                  </li>
                  <li>
                    <b>Score deterministically, then judge.</b> Tool-use and
                    safety checks are scored from the trace by deterministic
                    rules; open-ended correctness and faithfulness use a
                    calibrated LLM claim-checker against the case's reference
                    context.
                  </li>
                  <li>
                    <b>Compute reliability across runs.</b> A case counts as
                    reliable only if it passes on <i>all</i> <code>k</code> runs
                    (pass^k), not just once — surfacing flakiness a single pass@1
                    would hide.
                  </li>
                  <li>
                    <b>Roll up &amp; renormalise.</b> Component means are combined
                    using the weights above, renormalised over whichever
                    components the run produced, into the 0–100 Agenttic Index —
                    with every component shown alongside it.
                  </li>
                </ol>
              </div>

              {/* ---- datasets ---- */}
              <div className="mth-block" id="datasets">
                <SectionHeading eyebrow="Provenance"
                  title="Real public datasets."
                  sub={"Beyond the methodology-on-seed-data metrics, Agenttic "
                    + "ingests these real public benchmarks into labeled suites "
                    + "for direct comparability. Each carries its upstream "
                    + "license and citation."} />
                <div className="lp-grid lp-grid--2">
                  {datasets.map((d) => (
                    <div className="lp-cell mth-ds__card" key={d.dataset_id}>
                      <div className="mth-ds__top">
                        <span className="mth-ds__name">{d.name}</span>
                        <span className="mth-ds__meta">
                          {d.gated && (
                            <span className="mth-tag is-gated"
                                  title="Access-gated upstream — accept the dataset's terms / bring your own access token">
                              Gated
                            </span>
                          )}
                          <span className="mth-tag">{d.license}</span>
                        </span>
                      </div>
                      <p className="mth-ds__cite">{d.citation}</p>
                      {d.caveat && (
                        <p className="mth-ds__caveat">
                          <span aria-hidden="true">⚠</span> {d.caveat}
                        </p>
                      )}
                      <div className="mth-ds__foot">
                        <code>{d.suite_id}</code>
                        {d.source_url && (
                          <a href={d.source_url} target="_blank" rel="noreferrer">
                            Source ↗
                          </a>
                        )}
                      </div>
                    </div>
                  ))}
                  {/* the shared 1px-gap grid shows its own background through an
                      unfilled cell — fill it rather than print a grey block */}
                  {datasets.length % 2 === 1 && <div className="lp-cell" aria-hidden="true" />}
                </div>
                <p className="mth-source">
                  {live.datasets
                    ? "Live from /api/standard/datasets."
                    : "Showing the catalog of supported datasets (live endpoint unavailable)."}
                </p>
              </div>

              {/* ---- certification ---- */}
              <div className="mth-block" id="credential">
                <SectionHeading eyebrow="Qualifying"
                  title="From Index to a safety grade." />
                <div className="mth-cert">
                  <Seal grade="A" size={104} />
                  <p>
                    An <b>Agent Safety Certification</b> turns a scored run into a
                    single letter grade you can publish. The grade is drawn from
                    the 0–100 Agenttic Index above, but it never stands alone: the
                    certificate always shows the per-dimension safety breakdown
                    next to it, so a grade can't hide a weak injection-robustness
                    or refusal number.
                  </p>
                </div>

                <h3 className="mth-h3">What the grade bands mean</h3>
                <div className="mth-bands">
                  {GRADE_BANDS.map((b, i) => {
                    const next = GRADE_BANDS[i - 1];
                    const range = next ? `${b.min}–${next.min - 1}` : `≥ ${b.min}`;
                    const top = i === 0 ? `${b.min}–100` : range;
                    return (
                      <div className="mth-band" key={b.grade}>
                        <span className="mth-band__g"
                              style={{ color: gradeColor(b.grade),
                                       borderColor: gradeColor(b.grade) }}>
                          {b.grade}
                        </span>
                        <div className="mth-band__body">
                          <div className="mth-band__top">
                            <span className="mth-band__l">{b.label}</span>
                            <span className="mth-band__r">Index {top}</span>
                          </div>
                          <p>{b.blurb}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <h3 className="mth-h3">Which dimensions are tested</h3>
                <p className="mth-p">
                  The same literature-anchored components that feed the Index
                  decide the grade — weighted toward safety:{" "}
                  <b>prompt-injection robustness</b> (AgentDojo / InjecAgent),{" "}
                  <b>harmful-action refusal</b> (AgentHarm),{" "}
                  <b>secret-leak resistance</b>, <b>tool-call correctness</b>{" "}
                  (BFCL / τ-bench), <b>reliability across runs</b> (pass^k), and{" "}
                  <b>calibration</b>. Every one is shown on the public
                  certificate.
                </p>

                <h3 className="mth-h3">Pinned, signed, and verifiable</h3>
                <ul className="mth-sign">
                  <li>
                    <b>Pinned to a version.</b> A grade is bound to the exact
                    agent version it was earned on (a <code>config_hash</code>{" "}
                    over model, prompt, and tools). Change any of those and the
                    certificate no longer applies — re-test to re-certify. No
                    silent grade inflation.
                  </li>
                  <li>
                    <b>Independently verifiable.</b> The certificate payload is
                    signed with an <b>Ed25519</b> asymmetric signature. Agenttic
                    holds the private key; the matching <b>public key is
                    published</b> at{" "}
                    <code>/.well-known/agenttic-cert-keys.json</code>. So anyone —{" "}
                    <i>without trusting Agenttic and without any shared secret</i>{" "}
                    — can verify that the grade and scores on the public{" "}
                    <code>/certified/&#123;id&#125;</code> page were issued by
                    Agenttic and are unaltered: fetch the public key, then check
                    the certificate's <code>signature</code> over its{" "}
                    <code>signed_payload</code>. Each page also carries a clear
                    status — Valid, Expired, or Revoked. (The earlier scheme used
                    a symmetric secret only Agenttic could check; this replaces it
                    with true public verifiability.)
                  </li>
                  <li>
                    <b>Honest by construction.</b> Grades populate only from runs
                    that actually happened on Agenttic's suites — an agent with no
                    run has no grade, never an assumed pass. Revocation is one
                    click and is reflected publicly immediately.
                  </li>
                </ul>
              </div>

            </div>
          </details>
        </div>
      </section>

      {/* ---- CLOSE ------------------------------------------------------- */}
      <section id="enrol">
        <div className="wrap mth-close">
          <SectionHeading eyebrow="Enrol an agent"
            title="Watch one qualify, or start your own."
            sub={"Browse agents that already carry a credential with every "
              + "component score on show, or put your own through the first "
              + "drill."} />
          <div className="lp-cta">
            <Button href="/certified" variant="solid">Browse certified agents</Button>
            <Button href="/scan" variant="ghost">Start a training drill</Button>
          </div>
        </div>
      </section>

      </main>

      <footer className="wrap">
        <div className="lp-footer">
          <span className="mth-foot__brand">
            <HexMark size={13} /> Agenttic
          </span>
          <Link to="/">Home</Link>
          <Link to="/api-docs">API docs</Link>
          <Link to="/app/leaderboard">Leaderboard</Link>
          <span className="mth-foot__spacer" />
          <span>Safety testing for AI agents</span>
        </div>
      </footer>
    </div>
  );
}
