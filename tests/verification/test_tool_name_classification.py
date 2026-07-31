"""The MCP-namespace x camelCase matrix for `_lead_verb`.

Regression pin. `_lead_verb` does two things — strip an MCP namespace, then take
the leading token — and they were applied to two DIFFERENT strings: the namespace
was stripped from a local variable, the camelCase split re-read `s.name`, which
still carried it. So `mcp__acme__deleteAccount` produced the verb
`mcp__acme__delete`, matched no entry in `_WRITE_VERBS`, and came back
`risk_class == "unknown"` — neither a write nor irreversible, i.e. an agent that
deleted an account left a trace in which nothing dangerous appeared to happen.

Only one of the four cells was wrong, which is why it survived review: the
snake_case leaf (`mcp__acme__delete_account`) and the un-namespaced camelCase
name (`deleteAccount`) both worked, and the module docstring's example is a
snake_case leaf. The matrix is written out in full here so a future edit to
either half has to keep the other half honest.
"""

from __future__ import annotations

import pytest

from agenttic.verification.builtins import _is_irreversible, _lead_verb, risk_class

from .conftest import span


# (tool name, expected lead verb) across the full 2x2: namespaced or not,
# camelCase or snake_case.
MATRIX = [
    ("delete_account", "delete"),
    ("deleteAccount", "delete"),
    ("mcp__acme__delete_account", "delete"),
    ("mcp__acme__deleteAccount", "delete"),
]


@pytest.mark.parametrize("name,verb", MATRIX)
def test_lead_verb_is_the_same_across_the_namespace_and_case_matrix(name, verb):
    assert _lead_verb(span("tool_call", name)) == verb


@pytest.mark.parametrize("name,_verb", MATRIX)
def test_every_spelling_of_delete_account_is_a_write(name, _verb):
    assert risk_class(span("tool_call", name)) == "write"


@pytest.mark.parametrize("name,_verb", MATRIX)
def test_every_spelling_of_delete_account_is_irreversible(name, _verb):
    """A namespace is packaging, not semantics. If the un-namespaced name is an
    irreversible write, prefixing an MCP server onto it cannot make it safe."""
    assert _is_irreversible(span("tool_call", name)) is True


def test_namespaced_camelcase_verb_is_not_the_whole_prefix():
    """The exact shape of the bug: the verb came back with the namespace glued on."""
    assert _lead_verb(span("tool_call", "mcp__stripe__createRefund")) == "create"
    assert risk_class(span("tool_call", "mcp__stripe__createRefund")) == "write"


def test_read_verbs_survive_the_same_matrix():
    """The fix must not classify by 'has a scary noun somewhere'. A read verb
    still governs, namespaced or camelCased."""
    for name in ("get_charges", "getCharges",
                 "mcp__stripe__get_charges", "mcp__stripe__getCharges"):
        assert risk_class(span("tool_call", name)) == "read", name
        assert _is_irreversible(span("tool_call", name)) is False, name


def test_snake_case_is_not_camel_split():
    """`get_order` must not be re-split by the camelCase branch — the branch is
    entered only when the stripped name was a single word."""
    assert _lead_verb(span("tool_call", "get_order")) == "get"
    assert _lead_verb(span("tool_call", "mcp__acme__get_order")) == "get"


def test_empty_and_degenerate_names_yield_no_verb():
    """A name that is nothing but separators has no verb to find; `unknown` is
    the honest answer and must not raise."""
    for name in ("", "__", "mcp__acme__", "___"):
        assert _lead_verb(span("tool_call", name)) == ""
        assert risk_class(span("tool_call", name)) == "unknown"
