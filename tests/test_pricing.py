"""Model pricing → dollars (config-driven), and the shipped rate table itself.

The lookup helpers are only half the story: every published cost figure — the
report's "Scoring (judge)" line, the pre-run budget gate, ``total_scoring_cost_usd``
and the daily spend ledger — is that lookup times a rate that lives in a config
file. A wrong row there is invisible to every reader of the artifact, so the
shipped tables are pinned to the published per-MTok rates below.
"""

from pathlib import Path

import pytest
import yaml

from agenttic.cost import judge_model_for
from agenttic.pricing import model_price, token_cost

CFG = {"pricing": {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "default": {"input": 3.0, "output": 15.0},
}}

# Published Anthropic list prices, USD per 1M tokens (input, output).
# claude-opus-4-8 $5/$25 · claude-sonnet-4-6 $3/$15 · claude-haiku-4-5 $1/$5.
# A model priced in a shipped config with no entry here is a test failure by
# design: an unsourced rate is a fabricated figure.
PUBLISHED_PER_MTOK = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}

# Every config this project SHIPS, not just the two at the root. The third is
# the template `agenttic init` writes into a new user project — it is a shipped
# price list like the others, and it was the one place the $15/$75 error for
# claude-opus-4-8 survived the correction, because the guard below enumerated
# only two files. A new user would have started with a judge cost 3x reality.
SHIPPED_CONFIGS = ("config.yaml", "config.prod.yaml",
                   "src/agenttic/release/scaffold_assets/config.yaml")
_ROOT = Path(__file__).resolve().parents[1]


def _shipped(name: str) -> dict:
    return yaml.safe_load((_ROOT / name).read_text())


def test_known_model_price():
    assert model_price(CFG, "claude-opus-4-8") == {"input": 5.0, "output": 25.0}


def test_unknown_model_falls_back_to_default():
    assert model_price(CFG, "some-future-model") == {"input": 3.0, "output": 15.0}


def test_no_pricing_section_uses_builtin_default():
    assert model_price({}, "x") == {"input": 3.0, "output": 15.0}


def test_token_cost():
    # 1M input @ $5 + 1M output @ $25 = $30
    assert token_cost(CFG, "claude-opus-4-8", 1_000_000, 1_000_000) == 30.0
    # 1000 in / 500 out at default = (1000*3 + 500*15)/1e6
    assert token_cost(CFG, "x", 1000, 500) == (1000 * 3 + 500 * 15) / 1_000_000


def test_none_tokens_are_zero():
    assert token_cost(CFG, "x", None, None) == 0.0


class TestShippedRateTable:
    """The rate table the product actually bills, estimates and reports from."""

    @pytest.mark.parametrize("name", SHIPPED_CONFIGS)
    def test_every_priced_model_matches_the_published_rate(self, name):
        table = _shipped(name)["pricing"]
        for model, rate in table.items():
            if model == "default":       # policy fallback, not a vendor rate
                continue
            assert model in PUBLISHED_PER_MTOK, (
                f"{name}: '{model}' is priced with no published rate on record; "
                "add it to PUBLISHED_PER_MTOK with a source, or drop the row")
            got = {"input": float(rate["input"]), "output": float(rate["output"])}
            assert got == PUBLISHED_PER_MTOK[model], (
                f"{name}: '{model}' priced {got} but the published rate is "
                f"{PUBLISHED_PER_MTOK[model]} per MTok")

    @pytest.mark.parametrize("name", SHIPPED_CONFIGS)
    def test_every_model_the_product_calls_is_priced(self, name):
        """An unlisted model silently falls back to ``default`` — the cost is
        then reported with confidence and is wrong. Every configured model must
        have its own row."""
        cfg = _shipped(name)
        table = cfg["pricing"]
        for role, model in cfg["models"].items():
            assert model in table, (
                f"{name}: models.{role} = '{model}' has no pricing row, so its "
                "cost would be silently reported at the 'default' rate")

    def test_shipped_configs_do_not_disagree(self):
        """No two shipped price lists may disagree about the same model.

        Generalised from a two-way unpack to every pair, so adding a shipped
        config extends the check instead of breaking it. The two-way form is
        what let the scaffold template drift: it was not one of the two.
        """
        tables = {n: _shipped(n)["pricing"] for n in SHIPPED_CONFIGS}
        names = sorted(tables)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                for model in sorted(set(tables[a]) & set(tables[b])):
                    assert tables[a][model] == tables[b][model], (
                        f"'{model}' is priced differently in {a} vs {b}: "
                        f"{tables[a][model]} vs {tables[b][model]}")

    @pytest.mark.parametrize("name", SHIPPED_CONFIGS)
    def test_default_reference_agent_judge_leg_is_priced_at_opus(self, name):
        """models.judge_executor == models.agent_default, so judge selection
        falls back to judge_strong for the default reference agent: this rate
        is on the critical path of every judge call a default run makes."""
        cfg = _shipped(name)
        judge = judge_model_for(cfg, cfg["models"]["agent_default"])
        assert judge == cfg["models"]["judge_strong"] == "claude-opus-4-8"
        # $5 in + $25 out per MTok
        assert token_cost(cfg, judge, 1_000_000, 1_000_000) == 30.0
