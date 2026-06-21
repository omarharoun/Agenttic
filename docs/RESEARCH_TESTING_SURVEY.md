# Research Survey: AI-Agent Testing & Evaluation Benchmarks

**Purpose.** Ground agenttic's standard-benchmark roadmap in the published literature.
For each major agent-eval benchmark / harness from research papers we record its
real GitHub repo, paper, license, what it measures, the exact metric definitions,
dataset size & access, how its harness works, and how (or whether) we can ingest it
into agenttic's canonical-metric system. Ends with a **prioritized adoption roadmap**.

**How this maps to what we already have.** agenttic implements a UVM-style testbench
for agents: test registry → adapter-driven harness → standard-schema traces →
deterministic checks + LLM judge → scorecard + the **Agenttic Index**. We already
ingest three real public datasets via the `DatasetAdapter` pattern
(`src/ascore/metrics/datasets/`): **BFCL** (`bfcl-simple-v3`), **τ-bench**
(`tau-bench-v1`), and **AgentHarm** (`agentharm-harmful-v1`). Our canonical metrics
(`src/ascore/metrics/catalog.py`) are:

| Metric id | Methodology anchor | Index weight |
|---|---|---|
| `tool_call_accuracy` | BFCL / τ-bench tool-use (selection, params, sequence, abstention) | 0.35 |
| `harmful_refusal_rate` | AgentHarm refusal scoring | 0.175 |
| `injection_robustness` | AgentDojo / InjecAgent ASR (robustness = 1 − ASR) | 0.175 |
| `reliability_pass_k` | τ-bench pass^k | 0.20 |
| `calibration_ece` | Expected Calibration Error (Guo et al. 2017) + abstention | 0.10 |
| `faithfulness` | atomic-claim groundedness (FActScore / RAGAS-style) | (deferred) |

**Honesty stance (already in the catalog docstring).** We implement *methodologies*
on our own seed data; we do not reproduce any paper's exact numbers and do not claim
to *be* BFCL/τ-bench/etc. Adopting the public datasets for direct comparability is the
work this survey scopes.

**Ingestion difficulty legend used throughout:** 🟢 easy (static ground-truth →
existing canonical check) · 🟡 medium (needs a simulator, sandbox, or new fractional
metric) · 🔴 hard (live environment + LLM-judged, nondeterministic).

---

## 1. Tool / Function Use

### 1.1 BFCL — Berkeley Function-Calling Leaderboard  ✅ ingested
- **Repo:** [ShishirPatil/gorilla](https://github.com/ShishirPatil/gorilla) (`berkeley-function-call-leaderboard`; PyPI `bfcl`). [Blog #8](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html)
- **Paper:** No single BFCL paper; lineage is *Gorilla* ([arXiv:2305.15334](https://arxiv.org/abs/2305.15334), NeurIPS 2024), documented via release blogs.
- **License:** Apache-2.0. **HF:** `gorilla-llm/Berkeley-Function-Calling-Leaderboard`.
- **Measures:** correct function/tool calls across single, parallel, multiple, multi-turn, and *irrelevance* scenarios in several languages (Python/Java/JS/REST/SQL).
- **Versions:** v1 (simple/parallel/multiple via AST) · v2 "Live" (user-contributed, anti-contamination) · v3 (multi-turn/stateful) · v4 (agentic web search/memory — beyond our scope).
- **Metrics:** per-category **accuracy**, all-or-nothing (every call in a parallel set must match; no partial credit). **AST eval**: parse predicted call → check name, required params, types, values against an answer set (no execution). **Executable eval**: run the call, check exact / ±20% real-time numeric / structural match. **Relevance/irrelevance** category: scored correct only if the model **abstains** (no call) — measures over-triggering / hallucination resistance.
- **Size/access:** ~2,000 question-function-answer pairs in v1 + Live + multi-turn splits; ships in repo, mirrored on HF.
- **Ingestion:** 🟢 already done for the `simple` split. AST entries encode `{function_name, params}` → maps directly to `tool_selection_accuracy` + `tool_param_accuracy`; parallel/multiple → set-match `tool_sequence_accuracy`; irrelevance → `abstention_correct`. **Next:** add the *parallel*, *multiple*, and *live* splits to widen coverage; multi-turn (v3) needs the stateful env (🟡).

### 1.2 τ-bench  ✅ ingested
- **Repo:** [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench). **Paper:** "τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains," Yao et al., [arXiv:2406.12045](https://arxiv.org/abs/2406.12045), ICLR 2025. **License:** MIT.
- **Measures:** multi-turn tool-agent-user interaction. Agent (domain API tools + policy) converses with an **LLM-simulated user**; correctness = **final database state** vs annotated goal + required-output checks.
- **Domains:** τ-retail (115 tasks; 500 users/50 products/1,000 orders; 15 tools) · τ-airline (50 tasks; harder policy).
- **pass^k metric (the headline reliability idea we already adopted):** probability that **all k** i.i.d. trials of a task succeed. Unbiased estimator over n trials with c successes:
  `pass^k = E_task[ C(c,k) / C(n,k) ]`; `pass@k = E_task[ 1 − C(n−c,k)/C(n,k) ]`; `pass^1 = pass@1 = E[c/n]`. pass^k decays fast (gpt-4o ≈61% pass^1 → ≈25% pass^8 on retail), exposing flaky-in-prod failures.
- **Ingestion:** 🟡 We ingest the static task list and score tool-call accuracy/sequence + pass^k against our own runs. Full state-based scoring needs the user-simulator + live DB. **Next:** extend our `tau-bench-v1` adapter to attribute the annotated *goal write-actions* as the expected sequence, and run pass^k over k harness trials (we have the metric already).

### 1.3 τ²-bench (dual-control)
- **Repo:** [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench). **Paper:** "τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment," Barres et al., [arXiv:2506.07982](https://arxiv.org/abs/2506.07982) (2025). **License:** MIT.
- **New vs τ-bench:** both **agent and user** can call tools on a **shared dynamic state** (a Dec-POMDP); adds a **telecom** domain where the agent must *guide the user* to act on their own device; compositional task generator. pass@1 drops sharply in dual-control telecom (~34% for strong models). Same **pass^k** family.
- **Ingestion:** 🔴 correct behavior includes eliciting *user-side* tool calls, so an agent-only tool-call check under-captures it. Best mapped as a sequence check over the **combined** action log; needs the shared-state simulator. *(Exact telecom task counts unverified.)*

### 1.4 ToolBench / ToolLLM (ToolEval)
- **Repo:** [OpenBMB/ToolBench](https://github.com/OpenBMB/ToolBench). **Paper:** "ToolLLM: …16000+ Real-world APIs," Qin et al., [arXiv:2307.16789](https://arxiv.org/abs/2307.16789), ICLR 2024. **License:** Apache-2.0 (research/edu).
- **Scale:** 16,464 RapidAPI REST APIs / 3,451 categories / 126,486 instructions. Test sets sampled (200/test) across Instruction/Category/Tool generalization, difficulty groups I1–I3.
- **Metrics:** **Pass Rate** (instruction solved within an API-call budget) · **Win Rate** (LLM-judge pairwise preference vs a reference). Human agreement 87.1% (pass) / 80.3% (win). Introduces **DFSDT** (depth-first search decision tree) solving.
- **Access:** data via Google Drive / Tsinghua Cloud; needs a RapidAPI key / ToolBench server (live APIs decay — reproducibility friction).
- **Ingestion:** 🔴 pass/win are solvability + LLM-judge, not deterministic call-matching. Extract the annotated DFSDT path as an expected *sequence* (non-unique) with a judge fallback. Lower priority than cleaner static sets.

### 1.5 MCP-Bench
- **Repo:** [Accenture/mcp-bench](https://github.com/Accenture/mcp-bench). **Paper:** [arXiv:2508.20453](https://arxiv.org/abs/2508.20453) (2025). **License:** Apache-2.0.
- **Measures:** agents driving real **Model Context Protocol** servers (28 servers, single/2-/3-server tasks) — fuzzy-instruction understanding, cross-tool planning, dependency-aware sequencing, schema-correct invocation.
- **Metrics (averaged Overall):** rule-based **schema understanding** · tool-use effectiveness · planning · **dependency awareness** (ordering) · LLM-judged task completion (o4-mini judge).
- **Ingestion:** 🟡 schema-compliance + tool-selection → `tool_call_accuracy`; dependency awareness → `tool_sequence_accuracy`; planning/completion need a judge. Requires live MCP servers — **but** this is the most *directly relevant to where the ecosystem is going* (MCP), so worth a thin schema-only adapter.

### 1.6 Also noted (brief)
- **API-Bank** ([DAMO-ConvAI](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/api-bank), [arXiv:2304.08244](https://arxiv.org/abs/2304.08244), EMNLP 2023): 73 tools, 314 dialogues / 753 calls; **Accuracy** for calls + **ROUGE-L** for responses. 🟢 explicit ground-truth calls → direct tool-call + sequence mapping. *Strong easy-win candidate.*
- **ToolAlpaca** ([tangqiaoyu/ToolAlpaca](https://github.com/tangqiaoyu/ToolAlpaca), [arXiv:2306.05301](https://arxiv.org/abs/2306.05301), Apache-2.0): ~3,938 simulated instances; mostly a training corpus, eval is LLM-judged. 🟡

---

## 2. Safety / Prompt-Injection / Harm

### 2.1 AgentHarm  ✅ ingested
- **Repo:** [inspect_evals/agentharm](https://github.com/UKGovernmentBEIS/inspect_evals/tree/main/src/inspect_evals/agentharm). **Dataset:** [ai-safety-institute/AgentHarm](https://huggingface.co/datasets/ai-safety-institute/AgentHarm). **Paper:** Andriushchenko et al., [arXiv:2410.09024](https://arxiv.org/abs/2410.09024), ICLR 2025.
- **License:** ⚠️ **MIT + restriction** — "prohibit using the dataset … besides improving the safety and security of AI systems." Not pure OSS; the public set is a deliberate **subset** (44/66 public test base behaviors etc.).
- **Measures:** whether a tool-calling agent carries out explicitly malicious multi-step tasks, and how jailbreaks raise that. 110 base behaviors → 440 augmented across 11 harm categories, 104 tools; each harmful behavior has a **benign twin** (over-refusal detection).
- **Metrics (from `scorer.py`/`metric.py`):** **harm score** = mean over per-task hand-written grading dicts (correct tool/args/order + semantic-judge checks), ∈[0,1] · `avg_full_score` (fraction scoring exactly 1) · **refusal rate** = fraction of samples a dedicated **RefusalJudgeLLM** flags · `avg_score_non_refusals` (capability on non-refused). Refusal and score are independent fields.
- **Ingestion:** 🟢 already done (`agentharm-harmful-v1`) — it natively emits a refusal rate and a normalized harm score that map onto `harmful_refusal_rate`. **Note the license restriction** for any published index. It is *direct-misuse*, not injection.

### 2.2 AgentDojo  ⬅ high-value next
- **Repo:** [ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo). **Paper:** Debenedetti et al., [arXiv:2406.13352](https://arxiv.org/abs/2406.13352), NeurIPS 2024 D&B. **License:** MIT.
- **Measures:** robustness of tool-calling agents to **indirect prompt injection** in tool/environment outputs, with **deterministic state-based** checks (no LLM env-sim).
- **Size:** 97 user tasks + 27 injection tasks across 4 suites (Workspace/Slack/Travel/Banking), 74 tools, **629 security test cases** (user×injection cross-product).
- **Metrics:** **Benign Utility** (tasks solved, no attack) · **Utility Under Attack** (task solved + no adversarial side effect) · **Targeted ASR** (attacker goal achieved, per-injection `security()` state check). Attack types: "ignore previous," TODO, InjecAgent-style, **"Important message"** (strong default), adaptive/max. Best agents <66% benign utility; ASR <25% undefended → ~8% with a detector defense.
- **Ingestion:** 🟡 **cleanest injection benchmark** because checks are deterministic. Targeted ASR → `injection_robust` (`injection_robustness = 1 − ASR`); utility-under-attack is a second signal. Requires running the agent loop in their stateful env and adopting their suite/attack abstraction. **Top safety adoption target.** Also in `inspect_evals` (and NIST's `agentdojo-inspect` fork) — another reason to be Inspect-compatible.

### 2.3 InjecAgent  ⬅ easiest injection win
- **Repo:** [uiuc-kang-lab/InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent). **Paper:** Zhan et al., [arXiv:2403.02691](https://arxiv.org/abs/2403.02691), Findings of ACL 2024. **License:** MIT.
- **Measures:** indirect prompt injection in tool results. **1,054 test cases**, 17 user + 62 attacker tools; **base** vs **enhanced** (hacking-prompt) settings. Categories: **Direct Harm** vs **Data Stealing** (two-stage S1 retrieve / S2 exfiltrate).
- **Metrics:** **ASR** = fraction where agent performs attacker action (parsed from its tool call). **ASR-valid** (over parseable outputs only) vs **ASR-all** (over all 1,054). ReAct GPT-4 ≈24% ASR, ~doubling under enhanced.
- **Ingestion:** 🟢 **static JSON test cases** (user instruction + malicious tool response + expected attacker action) — no live env needed. ASR → `injection_robust` directly. Pick ASR-valid vs ASR-all to match our canonical definition; model S1-only vs full data-stealing as partial. **Best easy-win for injection coverage** (pairs with the harder AgentDojo).

### 2.4 R-Judge
- **Repo:** [Lordog/R-Judge](https://github.com/Lordog/R-Judge) · [site](https://rjudgebench.github.io/). **Paper:** Yuan et al., [arXiv:2401.10019](https://arxiv.org/abs/2401.10019), Findings of EMNLP 2024. **License:** ⚠️ **none declared** (no LICENSE file → treat as all-rights-reserved).
- **Measures:** not whether an agent acts unsafely, but whether an **LLM-as-judge can recognize** safety risk in a recorded trajectory. 569 multi-turn records, 27 scenarios, 5 app categories, 10 risk types; binary safety label + risk description.
- **Metrics:** Safety Judgment — **Recall**, **Specificity**, **F1** (primary). Risk Identification — GPT-4-graded **Effectiveness** on unsafe cases. GPT-4o ≈74% F1; human ceiling 89%.
- **Ingestion:** 🟡 *different shape* — a **judge/guard-model benchmark**, not agent execution. Doesn't yield refusal rate or ASR. Use it to **validate our own judge/guard**: F1/Recall on its labeled transcripts → a *judge-calibration* dataset (complements our `tests/test_judge_calibration.py`). License absence blocks redistribution — link, don't vendor.

### 2.5 ToolEmu
- **Repo:** [ryoungj/toolemu](https://github.com/ryoungj/toolemu). **Paper:** Ruan et al., [arXiv:2309.15817](https://arxiv.org/abs/2309.15817), ICLR 2024 (Spotlight). **License:** Apache-2.0.
- **Measures:** GPT-4 **emulates tool execution** from specs only (no real impls) for scalable red-teaming; scores **safety** and **helpfulness** via LM evaluators. 36 toolkits / 311 tools / 144 cases, 9 risk types.
- **Metrics:** **Safety / tool-call-risk 0–3** (likelihood × severity; 0 = likely severe, 3 = certain no risk) · **Helpfulness 0–3** (penalizes inaction). Captures the safety/utility tradeoff.
- **Ingestion:** 🔴 both env and scoring are LLM-driven (nondeterministic, GPT-4-cost-heavy). No native refusal/ASR — derive from the 0–3 rubric. Emulation-only (no real side effects), and it's *risky-direct-task*, not injection. Low priority; mine its **risk taxonomy** rather than ingest.

**Safety cross-map:**

| Benchmark | Native refusal? | Native ASR? | Determinism | Map difficulty |
|---|---|---|---|---|
| AgentHarm | **yes** + harm 0–1 | no | LLM judges | 🟢 (done) |
| AgentDojo | derivable | **yes** (targeted) | deterministic | 🟡 |
| InjecAgent | ≈1−ASR | **yes** (valid/all) | deterministic parse | 🟢 |
| R-Judge | no (judge bench) | no | F1 det.; Effect via GPT-4 | 🟡 (judge-cal) |
| ToolEmu | derive | no | LLM-judged | 🔴 |

---

## 3. Code Agents

### 3.1 SWE-bench / SWE-bench Verified
- **Repo:** [princeton-nlp/SWE-bench](https://github.com/princeton-nlp/SWE-bench). **Paper:** Jimenez et al., [arXiv:2310.06770](https://arxiv.org/abs/2310.06770), ICLR 2024 (Oral). **License:** MIT (code). Verified subset built with OpenAI.
- **Measures:** given a repo snapshot + GitHub issue, produce a **patch** that resolves it (real Python OSS: django, sympy, scikit-learn…).
- **Metrics:** **% Resolved** — an instance resolves **iff all FAIL_TO_PASS tests pass AND all PASS_TO_PASS tests pass** after applying the patch (binary per instance). FAIL_TO_PASS = tests tied to the fix (fail before, pass after gold PR); PASS_TO_PASS = regression guard.
- **Size/access:** test 2,294 · **Lite 300** · **Verified 500** · Multimodal 517. HF `SWE-bench/*` (public, non-gated).
- **Ingestion:** 🔴 **execution-gated** — needs a Docker per-instance test sandbox to apply a patch and run two named test sets (AND-combined). Best modeled as a **new `resolve_rate` canonical check** backed by a containerized runner. Highest *signal* but highest *infra cost*; defer until we want a code-agent track.

---

## 4. General / Web

### 4.1 GAIA
- **Host:** [gaia-benchmark/GAIA](https://huggingface.co/datasets/gaia-benchmark/GAIA) (**gated**). **Paper:** Mialon et al., [arXiv:2311.12983](https://arxiv.org/abs/2311.12983), ICLR 2024. **License:** ⚠️ none declared; gated.
- **Measures:** general-assistant capability — real questions needing multi-step reasoning + web/tool use + multimodality. 3 difficulty levels.
- **Metric:** normalized **exact-match accuracy** (single unambiguous answer, no partial credit).
- **Size:** 466 total = 165 validation (public answers) + 301 test (private, leaderboard-scored).
- **Ingestion:** 🟢 maps onto a **normalized exact-match** check (new but tiny). Only the 165-item validation split is locally scorable (test is leaderboard-gated). Multimodal attachments carried as task assets.

### 4.2 WebArena (+ VisualWebArena)
- **Repo:** [web-arena-x/webarena](https://github.com/web-arena-x/webarena). **Paper:** Zhou et al., [arXiv:2307.13854](https://arxiv.org/abs/2307.13854), ICLR 2024. **License:** Apache-2.0.
- **Measures:** autonomous web agents via **functional correctness** across self-hosted Docker sites (e-commerce, CMS admin, Reddit clone, GitLab, OpenStreetMap, Wikipedia). **812 tasks.**
- **Metrics:** programmatic evaluators (`evaluation_harness/evaluators.py`): StringEvaluator (`exact_match`/`must_include`/`fuzzy_match` LLM-judge/`ua_match`), URLEvaluator, HTMLContentEvaluator (`program_html`), composed by **multiplying** sub-scores → ~binary end-state correctness. Checks end *state*, not steps.
- **Ingestion:** 🔴 needs a **task-success-rate** metric + live Docker site stack; string sub-checks reuse our string/judge checks but URL/HTML checks need env introspection. Heavy.

### 4.3 AgentBench
- **Repo:** [THUDM/AgentBench](https://github.com/THUDM/AgentBench). **Paper:** Liu et al., [arXiv:2308.03688](https://arxiv.org/abs/2308.03688), ICLR 2024. **License:** Apache-2.0.
- **Measures:** LLMs as agents across **8 environments** (OS, DB, KG, Card Game, Lateral-Thinking Puzzles, ALFWorld, WebShop, Mind2Web).
- **Metric:** per-env **Success Rate**, aggregated. Server–client containerized envs.
- **Ingestion:** 🔴 heterogeneous; OS/DB are execution-gated. Model as a `task_success_rate` family parameterized per env. Defer.

### 4.4 AssistantBench
- **Repo:** [oriyor/assistantbench](https://github.com/oriyor/assistantbench). **Paper:** Yoran et al., [arXiv:2407.15711](https://arxiv.org/abs/2407.15711), EMNLP 2024. **License:** ⚠️ **AI Pubs Open RAIL-S** (use-restricted, not OSS).
- **Measures:** web agents on realistic, time-consuming open-web multi-hop tasks. 214 tasks (~33 dev / ~181 test), >525 pages / 258 sites.
- **Metrics:** answer-type-aware **accuracy** — strings: token **F1**; numbers: order-of-magnitude penalty `max(0, 1 − log(max(A,A′)/min(A,A′)))`; lists/JSON: per-key F1. Plus **answer rate** (non-abstention) and **precision** (accuracy on answered). Surfaces a precision/answer-rate tradeoff; no model >~26 accuracy.
- **Ingestion:** 🟡 needs a **fractional `answer_accuracy` check** (F1/ratio/keyed-F1) + an **`answer_rate`/abstention** companion — which dovetails with our calibration/abstention work. RAIL-S restriction: link/score, careful on redistribution.

---

## 5. Hallucination / Faithfulness

### 5.1 FActScore
- **Repo:** [shmsw25/FActScore](https://github.com/shmsw25/FActScore). **Paper:** Min et al., [arXiv:2305.14251](https://arxiv.org/abs/2305.14251), EMNLP 2023. **License:** MIT.
- **Measures:** factual **precision** of long-form text. Decompose output into **atomic facts** → judge each supported/unsupported vs a knowledge source → score = supported fraction (`init_score`), plus a length-penalized `score`. Precision, not recall.
- **Ingestion:** 🟢 the right model for our deferred `faithfulness` metric over `final_output`. Atomic-claim groundedness against `retrieval` spans / provided source.

### 5.2 RAGAS
- **Repo:** [explodinggradients/ragas](https://github.com/explodinggradients/ragas). **License:** Apache-2.0.
- **Metric definitions** (reference-light / LLM-judge): **Faithfulness** = supported claims / total claims ([doc](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)) · **Answer Relevancy** (mean cosine sim of answer-generated questions to the real question) · **Context Precision@K** = Σ(Precision@k·vₖ)/(#relevant in top-K) ([doc](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)) · **Context Recall** (ground-truth claims attributable to retrieved context).
- **Ingestion:** 🟢 our `retrieval` spans give exactly these inputs. Implement Faithfulness + Context Precision/Recall + Answer Relevancy as canonical checks. Apache-2.0 → vendor the definitions.

### 5.3 HaluEval
- **Repo:** [RUCAIBox/HaluEval](https://github.com/RUCAIBox/HaluEval). EMNLP 2023. **License:** MIT.
- **Measures:** hallucination **recognition**. **35K** samples: QA 10K (HotpotQA), Dialogue 10K (OpenDialKG), Summarization 10K (CNN/DM), + 5K human-annotated ChatGPT queries. Model identifies the hallucinated response.
- **Ingestion:** 🟢 static labeled data → a *recognition* accuracy/F1 check or judge-calibration set. Easy vendoring.

### 5.4 MIRAGE-Bench (agentic)
- **Repo:** [sunblaze-ucb/mirage-bench](https://github.com/sunblaze-ucb/mirage-bench). **Paper:** Zhang et al., [arXiv:2507.21017](https://arxiv.org/abs/2507.21017) (2025) — *Measuring Illusions in Risky AGEnt settings*. **License:** Apache-2.0. (Distinct from the multilingual-RAG "MIRAGE-Bench".)
- **Measures:** hallucination in interactive **agent** settings. Taxonomy: actions unfaithful to (i) task instructions, (ii) execution history, (iii) environment observations. **Hallucination rate** + **utility**, via a risk-aware **LLM-as-judge** ("snapshot" strategy isolates deterministic decision points).
- **Ingestion:** 🟡 the most agent-native faithfulness benchmark — its 3-way unfaithfulness taxonomy is a great structure for our `faithfulness` metric on traces (we have execution-history + observation spans). Judge-based. *(Exact counts unverified.)*

### 5.5 TruthfulQA (brief)
- Lin et al., [arXiv:2109.07958](https://arxiv.org/abs/2109.07958), ACL 2022. 817 questions / 38 categories targeting human misconceptions; % truthful (GPT-judge / MC). Static, easy, but not agentic.

---

## 6. Eval Frameworks / Harnesses

### 6.1 UK AISI **Inspect** — the standard to match
- **Repo:** [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) · [docs](https://inspect.aisi.org.uk/). **License:** MIT. Companion catalog: [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals) (200+ evals, incl. AgentHarm & AgentDojo).
- **Model:** **Task = Dataset + Solver + Scorer.** Dataset = `Sample(input, target)`. Solver = answer strategy (chainable: `generate()`, `system_message()`, tools, `self_critique()`, full `react()` agent loop with bash/python/web/MCP tools, Docker/K8s sandbox). Scorer = `model_graded_qa()` / `includes()` / `match()` / custom.
- **Why match it:** emerging government-standard agent-eval harness; its triad is **1:1** with agenttic's test-case → harness run → scoring/judge split. Matching it buys the whole `inspect_evals` catalog (AgentHarm, AgentDojo, AssistantBench…) for comparability.

### 6.2 OpenAI evals
- [openai/evals](https://github.com/openai/evals), MIT. Registry of evals + **Completion Function Protocol** (abstracts model/chain/agent). Templates: Match/Includes/FuzzyMatch + YAML model-graded. Borrow the registry + completion-fn pattern.

### 6.3 EleutherAI lm-evaluation-harness
- [EleutherAI/lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness), MIT. De-facto academic harness (60+ benchmarks); declarative **YAML tasks + Jinja2** prompts; backs the Open LLM Leaderboard. Static/non-agentic — borrow its YAML task-config ergonomics only.

### 6.4 HELM
- [stanford-crfm/helm](https://github.com/stanford-crfm/helm), Apache-2.0. **Scenarios × metrics** matrix — accuracy + calibration, robustness, fairness, toxicity, efficiency. Borrow the multi-metric **scorecard shape** for `schema/scorecard.py` (our Agenttic Index is a sibling idea).

### 6.5 DeepEval
- [confident-ai/deepeval](https://github.com/confident-ai/deepeval), Apache-2.0. **Pytest-style** LLM eval; **G-Eval** (criteria-driven LLM-judge w/ CoT + score bands); built-in Hallucination/Faithfulness/Answer-Relevancy + RAG metrics. Align our rubric schema to a G-Eval-style structure so judges are portable; our `test_judge_calibration.py` is already building this.

### 6.6 Promptfoo / Langfuse
- [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) (MIT): config-driven eval + **red-team** scanning; CI/CD. [langfuse/langfuse](https://github.com/langfuse/langfuse) (MIT except `ee/`): OSS **observability/tracing + evals** with **OpenTelemetry** ingestion. Since our trace schema is already OTel-GenAI-named, Langfuse is near-zero-cost interop for live/production traces (`test_case_id=None`).

---

## 7. Prioritized Adoption Roadmap

Ranked by **value × tractability**. "Value" = comparability + coverage of a metric we
claim; "Tractability" = how close it is to our existing `DatasetAdapter` + canonical
checks. Items 1–4 are static-data adapters that reuse checks we already have.

| # | Adopt | Why (value) | Effort | New metric/infra? |
|---|---|---|---|---|
| **1** | **InjecAgent** (`injecagent-v1` adapter) | We *claim* `injection_robustness` but have no real injection dataset. 1,054 static cases, MIT, ASR→`injection_robust` directly. Closes our biggest credibility gap. | 🟢 low | none (reuse `injection_robust`) |
| **2** | **AgentDojo** (`agentdojo-v1`) | Gold-standard injection benchmark, **deterministic** ASR + utility-under-attack; pairs with InjecAgent for breadth+depth. MIT, also in `inspect_evals`. | 🟡 med | run agent loop in its env; reuse `injection_robust` + add `utility_under_attack` |
| **3** | **BFCL parallel/multiple/live splits** + **API-Bank** | Widen `tool_call_accuracy` beyond `simple`. API-Bank adds 753 ground-truth calls (Apache/clean), parallel/multiple exercise our set-match `tool_sequence_accuracy`. | 🟢 low | none |
| **4** | **Faithfulness metric, real** (FActScore + RAGAS defs; HaluEval as judge-cal) | Turn the deferred `faithfulness` (weight 0) into a live metric: atomic-claim groundedness over `final_output`/`retrieval` spans. MIT/Apache. Unlocks the 6th Index component. | 🟡 med | implement `faithfulness` check + atomic-claim decomposition |
| **5** | **Inspect interop layer** (importer/exporter) | Make our `Trace` ↔ Inspect `EvalLog`/transcript lossless. Buys the entire `inspect_evals` catalog and credibility-by-association with the gov standard. Our Task/Solver/Scorer split already matches. | 🟡 med | adapter only (no new metric) |
| **6** | **τ-bench pass^k on real runs** (finish `tau-bench-v1`) | We have the pass^k metric and the static tasks; wire k-trial harness runs + goal-action sequence extraction to report real reliability numbers. | 🟡 med | reuse `reliability_pass_k` |
| **7** | **AssistantBench-style `answer_accuracy` + `answer_rate`** | Adds graded (non-binary) answer scoring + a real **abstention** signal that strengthens our calibration track. Watch RAIL-S license (score, don't redistribute). | 🟡 med | new fractional `answer_accuracy` + `answer_rate` |
| **8** | **SWE-bench Verified** (code-agent track) | Highest external recognition; but execution-gated Docker harness is a large infra lift. Do last, as a separate `resolve_rate` track. | 🔴 high | `resolve_rate` check + container test runner |

**Deferred / mine-for-ideas, don't ingest:** τ²-bench (dual-control simulator — 🔴),
ToolBench (decaying live APIs + LLM-judge), ToolEmu (LLM-emulated, nondeterministic —
take its risk taxonomy), R-Judge (no license — use only to validate our judge),
WebArena/AgentBench (heavy live Docker env stacks), GAIA (gated, leaderboard-only test
split — ingest only the 165 public validation items if at all).

### What makes our published index most credible & comparable
1. **Cite + license-tag every dataset** in the scorecard (we already ship `ATTRIBUTION.md`
   + `LICENSE` per adapter) — and **honor restrictions**: AgentHarm (MIT+safety-only),
   AssistantBench (RAIL-S), GAIA (gated), R-Judge (no license) must be *scored-but-not-redistributed*.
2. **Report the metric exactly as the source defines it** (e.g. AgentDojo *targeted* ASR
   vs InjecAgent ASR-valid vs ASR-all) and say which variant we use — half-credit drift
   is the fastest way to lose comparability.
3. **Be Inspect/OTel-compatible** (items 5 + Langfuse) so third parties can re-run our
   suites in the harness they already trust; comparability is a property of the *harness*,
   not just the numbers.
4. **Always show components, never just the rollup** — our Index already renormalizes over
   present metrics and reports `missing`; keep that honesty (a missing metric is named, not
   hidden).
5. **Separate "methodology on our seed data" from "real public dataset" suites** in the
   published index, so readers know which rows are directly comparable to the source
   leaderboard and which are agenttic-internal.

---

*Sources are linked inline. Items flagged unverified: τ²-bench telecom task counts,
MIRAGE-Bench scenario counts, BFCL HF dataset-card details, RAGAS Context-Recall exact
formula, SWE-bench HF data-license, GAIA/AssistantBench split numbers (minor). All repo
URLs, papers, licenses, and core metric definitions were confirmed against primary sources.*
