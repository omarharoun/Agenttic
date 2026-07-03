"""Faithful port of BFCL's official AST checker — correctness + anti-gaming.

The port exists so real model predictions are graded under the SAME semantics as
the published Python Simple AST number. These tests guard that it is FAITHFUL,
not lenient: it credits BFCL's documented normalisations (string/number/list),
and it STILL REJECTS genuinely wrong answers (wrong function, missing/extra/
wrong-typed/wrong-valued params). If it stopped rejecting wrong answers, the
reproduced number would be meaningless.
"""

from __future__ import annotations

from ascore.metrics.bfcl_ast_official import (
    simple_function_correct,
    standardize_string,
)

# A representative function: one int, one string, one float, one array, one dict,
# with an optional param — enough to exercise every code path.
_FD = {
    "name": "make_thing",
    "parameters": {
        "type": "dict",
        "properties": {
            "count": {"type": "integer"},
            "label": {"type": "string"},
            "ratio": {"type": "float"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "opts": {"type": "dict"},
            "note": {"type": "string"},           # optional
        },
        "required": ["count", "label"],
    },
}
_PA = {"make_thing": {
    "count": [3],
    "label": ["Blue Widget"],
    "ratio": [1.5],
    "tags": [["a", "b"]],
    "opts": [{"color": ["red"]}],
    "note": ["", "hello"],   # optional
}}


def _ok(args):
    return simple_function_correct(_FD, {"make_thing": args}, _PA)[0]


class TestStandardize:
    def test_matches_bfcl_normalisation(self):
        # exactly the upstream rule: strip spaces + , . / - _ * ^, lowercase
        assert standardize_string("2*x**2") == standardize_string("2x**2")
        assert standardize_string("engine size") == standardize_string("engine_size")
        assert standardize_string("April 1, 2024") == standardize_string("April 1 2024")
        # genuinely different strings do NOT collapse
        assert standardize_string("human cell") != standardize_string("human")


class TestCreditsGenuineEquivalences:
    def test_exact(self):
        assert _ok({"count": 3, "label": "Blue Widget", "ratio": 1.5,
                    "tags": ["a", "b"], "opts": {"color": "red"}})

    def test_string_normalisation_credited(self):
        # "blue_widget" ≡ "Blue Widget" under BFCL normalisation
        assert _ok({"count": 3, "label": "blue_widget", "ratio": 1.5,
                    "tags": ["a", "b"], "opts": {"color": "red"}})

    def test_int_accepted_for_float(self):
        # BFCL: an int is accepted where the schema says float
        assert _ok({"count": 3, "label": "Blue Widget", "ratio": 1,  # int, not 1.5
                    "tags": ["a", "b"], "opts": {"color": "red"}}) is False  # value wrong
        assert _ok({"count": 3, "label": "Blue Widget", "ratio": 1.5,
                    "tags": ["a", "b"], "opts": {"color": "red"}})

    def test_optional_param_may_be_omitted_or_present(self):
        base = {"count": 3, "label": "Blue Widget", "ratio": 1.5,
                "tags": ["a", "b"], "opts": {"color": "red"}}
        assert _ok(base)                          # 'note' omitted -> allowed ("")
        assert _ok({**base, "note": "hello"})     # 'note' present + allowed


class TestRejectsWrongAnswers:  # anti-gaming: these MUST fail
    _base = {"count": 3, "label": "Blue Widget", "ratio": 1.5,
             "tags": ["a", "b"], "opts": {"color": "red"}}

    def test_wrong_function_name(self):
        assert simple_function_correct(
            _FD, {"other_fn": self._base}, _PA)[0] is False

    def test_missing_required(self):
        args = dict(self._base)
        del args["label"]
        assert _ok(args) is False

    def test_extra_unexpected_param(self):
        assert _ok({**self._base, "bogus": 1}) is False

    def test_wrong_int_value(self):
        assert _ok({**self._base, "count": 99}) is False

    def test_wrong_string_value(self):
        assert _ok({**self._base, "label": "Green Gadget"}) is False

    def test_wrong_list_value(self):
        assert _ok({**self._base, "tags": ["a", "z"]}) is False

    def test_wrong_dict_value(self):
        assert _ok({**self._base, "opts": {"color": "purple"}}) is False

    def test_wrong_dict_key(self):
        assert _ok({**self._base, "opts": {"shade": "red"}}) is False

    def test_wrong_type(self):
        # a string where an integer is required
        assert _ok({**self._base, "count": "three"}) is False


class TestOracleAndScorerIntegration:
    def test_oracle_scores_100_percent_official(self):
        from ascore.metrics.bfcl_reproduce import (
            load_simple_python_v4,
            validate_official_scorer,
        )
        cases = load_simple_python_v4()
        sc = validate_official_scorer(cases)
        assert sc.n >= 390
        assert sc.accuracy == 1.0        # a correct checker credits all gold answers

    def test_official_scorer_rejects_a_broken_prediction(self):
        # swap in a wrong function name for one case -> that case must fail
        from ascore.metrics.bfcl_reproduce import (
            load_simple_python_v4,
            official_oracle_predictions,
            score_cases_official,
        )
        cases = load_simple_python_v4()
        preds = official_oracle_predictions(cases)
        bid = cases[0].expected["bfcl_id"]
        preds[bid] = [{"name": "__wrong__", "args": {}}]
        sc = score_cases_official(cases, preds)
        assert sc.passes == sc.n - 1
