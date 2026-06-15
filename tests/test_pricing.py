"""Model pricing → dollars (config-driven)."""

from ascore.pricing import model_price, token_cost

CFG = {"pricing": {
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "default": {"input": 3.0, "output": 15.0},
}}


def test_known_model_price():
    assert model_price(CFG, "claude-opus-4-8") == {"input": 15.0, "output": 75.0}


def test_unknown_model_falls_back_to_default():
    assert model_price(CFG, "some-future-model") == {"input": 3.0, "output": 15.0}


def test_no_pricing_section_uses_builtin_default():
    assert model_price({}, "x") == {"input": 3.0, "output": 15.0}


def test_token_cost():
    # 1M input @ $15 + 1M output @ $75 = $90
    assert token_cost(CFG, "claude-opus-4-8", 1_000_000, 1_000_000) == 90.0
    # 1000 in / 500 out at default = (1000*3 + 500*15)/1e6
    assert token_cost(CFG, "x", 1000, 500) == (1000 * 3 + 500 * 15) / 1_000_000


def test_none_tokens_are_zero():
    assert token_cost(CFG, "x", None, None) == 0.0
