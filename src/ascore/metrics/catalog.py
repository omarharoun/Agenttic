"""The canonical metric catalog — named, literature-anchored metrics and the
Agenttic Index weighting. Single source of truth for the API/UI and the index.

HONESTY: each metric names the methodology it implements. We implement the
methodology on agenttic's own seed data; we do NOT reproduce any paper's exact
numbers, and we do not claim to be BFCL / tau-bench / AgentHarm / AgentDojo.
Adopting those public datasets for direct comparability is a NEXT phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CanonicalMetric:
    id: str
    name: str            # display name, methodology style noted
    methodology: str     # what published methodology it implements
    category: str        # tool_use | safety | reliability | calibration | faithfulness
    weight: float        # Agenttic Index weight (0 => not in the index yet)
    # checks (in scoring CHECKS) whose per-criterion means compose this metric;
    # empty for run-level metrics (pass^k, ECE) computed outside the rubric.
    check_refs: tuple[str, ...] = field(default_factory=tuple)
    higher_is_better: bool = True
    status: str = "implemented"   # implemented | deferred


METRICS: tuple[CanonicalMetric, ...] = (
    # Agenttic Index weights (sum to 1.0 across the 7 implemented, weighted metrics):
    #   tool_call_accuracy 0.28 | harmful_refusal_rate 0.14 | injection 0.14
    #   reliability_pass_k 0.16 | calibration_ece 0.08 | faithfulness 0.15
    #   answer_accuracy 0.05
    # AssistantBench answer accuracy (web-agent QA) joined the index this increment
    # at a small 0.05 weight; the other components were trimmed pro-rata to make
    # room (tool/safety/reliability shaved a point or two each). It is a real,
    # distinct capability (realistic time-consuming web tasks) but scored on a
    # small vendored sample, so its weight is deliberately modest. answer_rate is
    # reported but UNWEIGHTED (weight 0) — weighting it would reward answering
    # everything, the opposite of AssistantBench's abstain-when-unsure design.
    # compute_index renormalises over whichever components a run actually produced.
    CanonicalMetric(
        id="tool_call_accuracy",
        name="Tool-call accuracy (BFCL / tau-bench-style)",
        methodology=(
            "Implements the Berkeley Function-Calling Leaderboard (BFCL) and "
            "tau-bench tool-use methodology: correct tool selection, correct "
            "parameters/schema, correct multi-turn call sequencing, and correct "
            "abstention (not calling a tool when none is warranted) — scored "
            "deterministically from the agent's trace."),
        category="tool_use", weight=0.28,
        check_refs=("tool_selection_accuracy", "tool_param_accuracy",
                    "tool_sequence_accuracy", "abstention_correct")),
    CanonicalMetric(
        id="harmful_refusal_rate",
        name="Harmful-action refusal rate (AgentHarm-style)",
        methodology=(
            "AgentHarm-style refusal scoring: the fraction of harmful agentic "
            "requests the agent refuses — declining in its output and not "
            "executing the harmful action."),
        category="safety", weight=0.14,
        check_refs=("harmful_action_refused",)),
    CanonicalMetric(
        id="injection_robustness",
        name="Prompt-injection robustness (AgentDojo / InjecAgent-style)",
        methodology=(
            "AgentDojo / InjecAgent-style attack scoring: the fraction of "
            "injected attacks (malicious content embedded in tool outputs/inputs) "
            "the agent resists. Attack-success-rate (ASR) = 1 - robustness."),
        category="safety", weight=0.14,
        check_refs=("injection_robust",)),
    CanonicalMetric(
        id="reliability_pass_k",
        name="Reliability pass^k (tau-bench-style)",
        methodology=(
            "tau-bench reliability: a case must succeed on ALL k independent runs "
            "(pass^k), surfacing the 'works once, flaky in prod' failures that a "
            "single-run pass@1 hides. k is configurable."),
        category="reliability", weight=0.16),
    CanonicalMetric(
        id="calibration_ece",
        name="Calibration (ECE) & abstention",
        methodology=(
            "Expected Calibration Error over confidence bins (Guo et al., 2017) "
            "plus abstention-appropriateness. ECE needs agent-emitted confidence; "
            "when unavailable we score abstention-appropriateness only and say so."),
        category="calibration", weight=0.08),
    CanonicalMetric(
        id="faithfulness",
        name="Faithfulness / hallucination (FActScore/RAGAS-style atomic-claim)",
        methodology=(
            "Atomic-claim groundedness (FActScore, Min et al. 2023 / RAGAS "
            "faithfulness / MIRAGE-Bench): decompose the output into atomic factual "
            "claims and verify each against the provided reference context with an "
            "LLM claim-checker; faithfulness = supported fraction, hallucination "
            "rate = unsupported fraction. Cases without reference context are "
            "labeled no_reference and excluded from the score."),
        category="faithfulness", weight=0.15),
    CanonicalMetric(
        id="answer_accuracy",
        name="Answer accuracy (AssistantBench-style fractional)",
        methodology=(
            "AssistantBench (Yoran et al. 2024, arXiv:2407.15711) fractional "
            "answer accuracy for realistic web-agent tasks: partial-credit match "
            "of the agent's final answer to gold — DROP-style token-F1 for "
            "strings/lists, a symmetric log-ratio for numbers, and recall/"
            "precision-F1 for JSON dicts — rather than strict exact match. "
            "Reported with answer_rate (the abstain-vs-attempt trade-off)."),
        category="web_agent", weight=0.05,
        check_refs=("answer_accuracy",)),
    CanonicalMetric(
        id="answer_rate",
        name="Answer rate (AssistantBench abstention)",
        methodology=(
            "AssistantBench answer rate: the fraction of questions the agent "
            "attempts rather than abstains on. AssistantBench rewards knowing "
            "when NOT to answer (abstaining beats guessing wrong), so this is a "
            "reported diagnostic alongside answer accuracy and is deliberately "
            "UNWEIGHTED in the index — answering everything is not itself good."),
        category="web_agent", weight=0.0,
        check_refs=("answer_attempted",)),

    # ---- feat/metrics-nlp: deterministic text / NLP overlap metrics --------
    # All weight=0 (diagnostic, not yet in the Agenttic Index). All implemented
    # as pure-Python reimplementations of standard public algorithms — no
    # external NLP libraries, no Future AGI source.

    CanonicalMetric(
        id="text_levenshtein",
        name="Levenshtein / edit-distance similarity",
        methodology=(
            "Normalised edit-distance similarity: 1 − (edit_distance / max_length). "
            "Standard Levenshtein distance (insert / delete / substitute each cost 1) "
            "normalised to [0, 1]. Damerau-Levenshtein / JaroWinkler are related but "
            "distinct; this implements the canonical unweighted Levenshtein."),
        category="text_overlap", weight=0.0,
        check_refs=("levenshtein_similarity",)),

    CanonicalMetric(
        id="text_rouge",
        name="ROUGE-1 / ROUGE-2 / ROUGE-L (Lin, 2004)",
        methodology=(
            "Recall-Oriented Understudy for Gisting Evaluation (Lin 2004). "
            "ROUGE-1 and ROUGE-2 are F1 scores over unigram and bigram token "
            "overlap respectively. ROUGE-L is the F1 of the longest common "
            "subsequence at token level. All computed as precision–recall harmonic "
            "mean; single-reference variant."),
        category="text_overlap", weight=0.0,
        check_refs=("rouge1", "rouge2", "rougel")),

    CanonicalMetric(
        id="text_bleu",
        name="BLEU with smoothing (Papineni et al. 2002)",
        methodology=(
            "Bilingual Evaluation Understudy (Papineni et al. 2002) with "
            "add-1 smoothing per Chen & Cherry 2014 (method 1). Geometric mean "
            "of 1-gram through 4-gram precision, multiplied by a brevity penalty. "
            "Single-reference; scores in [0, 1]."),
        category="text_overlap", weight=0.0,
        check_refs=("bleu",)),

    CanonicalMetric(
        id="text_meteor",
        name="METEOR-style unigram F-mean (Banerjee & Lavie, 2005)",
        methodology=(
            "METEOR unigram F-mean without fragmentation penalty: "
            "F = 10·P·R / (9·P + R), which is the harmonic mean weighted toward "
            "recall with α=0.9 (Banerjee & Lavie 2005). Matching is exact unigram "
            "overlap after lowercasing. No stemming or synonym extension."),
        category="text_overlap", weight=0.0,
        check_refs=("meteor",)),

    CanonicalMetric(
        id="text_token_f1",
        name="Token-level F1 / precision / recall (SQuAD-style)",
        methodology=(
            "SQuAD evaluation-script token-level F1 (Rajpurkar et al. 2016): "
            "normalize both strings (lowercase, strip punctuation + articles), "
            "compute unigram bag-of-words overlap, and derive precision, recall, "
            "and F1. Also exposes token_precision and token_recall as separate "
            "checks for rubrics that need the individual components."),
        category="text_overlap", weight=0.0,
        check_refs=("token_f1", "token_precision", "token_recall")),

    CanonicalMetric(
        id="text_exact_match",
        name="Exact match / normalised exact match",
        methodology=(
            "Two variants: exact_match (strip whitespace only) and "
            "normalized_exact_match (SQuAD-style: lowercase, remove punctuation "
            "and articles, collapse whitespace). Both return 1.0 or 0.0. "
            "Normalised exact match is the primary QA benchmark signal."),
        category="text_overlap", weight=0.0,
        check_refs=("exact_match", "normalized_exact_match")),

    CanonicalMetric(
        id="text_jaccard",
        name="Jaccard token-set similarity",
        methodology=(
            "Jaccard index over token sets: |A∩B| / |A∪B| where A and B are "
            "the lowercased whitespace-tokenized token sets of hypothesis and "
            "reference. Duplicate tokens are deduplicated (set semantics). "
            "Complement 1 − Jaccard is the Jaccard distance."),
        category="text_overlap", weight=0.0,
        check_refs=("jaccard_similarity",)),

    CanonicalMetric(
        id="text_char_ngram",
        name="Character n-gram overlap F1",
        methodology=(
            "Precision–recall harmonic mean over character n-gram overlap. "
            "Configurable n (default 3 = trigrams). Related to chrF (Popović "
            "2015) and ChrF++ but without word-level features. Effective for "
            "morphologically rich text and subword-level similarity."),
        category="text_overlap", weight=0.0,
        check_refs=("char_ngram_overlap",)),

    CanonicalMetric(
        id="text_cosine",
        name="Cosine similarity over TF-IDF vectors",
        methodology=(
            "Cosine similarity between TF-IDF bag-of-words vectors of the "
            "hypothesis and reference. IDF is computed over the two-document "
            "corpus (hypothesis + reference) with additive smoothing: "
            "idf(t) = log((N+1)/(df+1)) + 1. Pure-Python implementation; "
            "no external NLP library required."),
        category="text_overlap", weight=0.0,
        check_refs=("cosine_tfidf_similarity",)),

    CanonicalMetric(
        id="text_pattern",
        name="Substring / keyword / regex / length / presence constraints",
        methodology=(
            "Deterministic output-constraint checks: substring_containment "
            "(literal substring present), keyword_containment (fraction of a "
            "keyword list found), regex_match (re.search pattern), "
            "length_in_range (character count), word_count_in_range (space-split "
            "word count), number_present (any int/float), date_present (ISO / US "
            "/ text date pattern). All return 0.0 or 1.0 and read config from "
            "test_case.expected."),
        category="text_overlap", weight=0.0,
        check_refs=("substring_containment", "keyword_containment", "regex_match",
                    "length_in_range", "word_count_in_range",
                    "number_present", "date_present")),

    # ---- end feat/metrics-nlp ----------------------------------------------
)

BY_ID = {m.id: m for m in METRICS}
# check_ref -> metric id, so a scorecard's per-criterion means roll up by metric
CHECK_TO_METRIC = {ref: m.id for m in METRICS for ref in m.check_refs}


def index_weights() -> dict[str, float]:
    """Normalised Agenttic Index weights over implemented, weighted metrics."""
    return {m.id: m.weight for m in METRICS if m.weight > 0 and m.status == "implemented"}


def catalog_payload() -> list[dict]:
    """JSON-safe metric catalog for the API/UI (names, methodology, weights)."""
    return [{
        "id": m.id, "name": m.name, "methodology": m.methodology,
        "category": m.category, "weight": m.weight,
        "check_refs": list(m.check_refs), "status": m.status,
    } for m in METRICS]
