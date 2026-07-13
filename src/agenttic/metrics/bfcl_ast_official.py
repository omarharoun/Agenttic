"""Faithful port of BFCL's OFFICIAL AST checker (Python simple category).

This mirrors ``bfcl_eval/eval_checker/ast_eval/ast_checker.py`` from the Gorilla
repo (ShishirPatil/gorilla, Apache-2.0) — the exact scorer the Berkeley
Function-Calling Leaderboard uses to compute "Python Simple AST". We port it so a
model's real predictions can be graded under the SAME semantics the published
number was computed with, rather than a stricter homegrown matcher.

ANTI-GAMING: this is a *faithful* port, NOT a loosened matcher. Every rule below
is a documented BFCL rule with the upstream line it comes from; nothing was
relaxed to reach a target number. The checker STILL rejects wrong answers (wrong
function, missing/extra/wrong-typed/wrong-valued params) — see the tests in
tests/test_bfcl_ast_official.py.

Ported rules (Python `simple` path of ``simple_function_checker``):

* **Function name** must appear in the model output (else fail).
* **Required params** must all be present (else fail).
* **No unexpected params**: any param the model emits that isn't in the function
  schema AND the gold answer fails (BFCL ``simple_function_checker:unexpected_param``).
* **Type check** (``type_checker``): value's Python type must equal the schema
  type (``PYTHON_TYPE_MAPPING``), with the "variable" escape hatch — if the gold
  allowed-values are strings (a symbolic variable) and the model returns that
  type, it's accepted as a variable.
* **int→float coercion**: an ``int`` is accepted where the schema says ``float``.
* **tuple→list**: a tuple value is compared as a list when the schema says tuple.
* **String normalization** (``standardize_string``): remove spaces and
  ``, . / - _ * ^`` punctuation, lowercase, and turn ``'`` into ``"`` — so
  ``2*x**2`` ≡ ``2x**2``, ``engine size`` ≡ ``engine_size``. Applied to string
  params, string elements of lists, and string values of dicts.
* **List / dict / list-of-dict** checkers apply the same string normalization
  element-wise; dict checker also enforces no unexpected/missing dict keys.
* **Optional params**: a gold param whose allowed-values include ``""`` may be
  omitted; one without ``""`` must be provided.

Only the Python branch is ported (the leaderboard number we target is Python
Simple AST). Java/JS type converters are out of scope and intentionally omitted.
"""

from __future__ import annotations

import re

# Upstream: PYTHON_TYPE_MAPPING (ast_checker.py:14-23)
PYTHON_TYPE_MAPPING = {
    "string": str, "integer": int, "float": float, "boolean": bool,
    "array": list, "tuple": list, "dict": dict, "any": str,
}
PYTHON_NESTED_TYPE_CHECK_LIST = ["array", "tuple"]


def standardize_string(input_string: str) -> str:
    """Upstream ``standardize_string`` (ast_checker.py:174-182), verbatim: strip
    spaces and ``, . / - _ * ^`` punctuation, lowercase, ``'`` → ``"``."""
    regex_string = r"[ \,\.\/\-\_\*\^]"
    return re.sub(regex_string, "", input_string).lower().replace("'", '"')


def _possible_answer_type(possible_answer: list):
    """Upstream ``get_possible_answer_type`` (ast_checker.py:76-80): the type of
    the first non-optional ("") allowed value."""
    for answer in possible_answer:
        if answer != "":
            return type(answer)
    return None


def _type_checker(value, possible_answer: list, expected_type_converted,
                  nested_type_converted) -> tuple[bool, bool]:
    """Port of ``type_checker`` (ast_checker.py:93-171), Python path. Returns
    ``(valid, is_variable)``."""
    is_variable = False
    pa_type = _possible_answer_type(possible_answer)
    if pa_type is not None and pa_type != expected_type_converted:
        is_variable = True

    if type(value) == expected_type_converted:  # noqa: E721 — BFCL uses exact type
        if nested_type_converted is None:
            return True, is_variable
        # nested: each element must match at least one allowed nested type list
        for possible_answer_item in possible_answer:
            flag = True
            if type(possible_answer_item) == list:  # noqa: E721
                for value_item in value:
                    ok, _ = _type_checker(value_item, possible_answer_item,
                                          nested_type_converted, None)
                    if not ok:
                        flag = False
                        break
            if flag:
                return True, is_variable
        return False, is_variable

    # value not of expected type — accept if it matches the "variable" type
    if pa_type is not None and type(value) == pa_type:  # noqa: E721
        return True, True
    return False, is_variable


def _string_checker(value: str, possible_answer: list) -> bool:
    """Upstream ``string_checker`` (ast_checker.py:185-201)."""
    std = standardize_string(value)
    allowed = [standardize_string(a) for a in possible_answer if type(a) == str]  # noqa: E721
    return std in allowed


def _list_checker(value: list, possible_answer: list) -> bool:
    """Upstream ``list_checker`` (ast_checker.py:204-235)."""
    std_out = list(value)
    for i in range(len(std_out)):
        if type(std_out[i]) == str:  # noqa: E721
            std_out[i] = standardize_string(std_out[i])
    std_allowed = []
    for i in range(len(possible_answer)):
        std_allowed.append([])
        for j in range(len(possible_answer[i])):
            item = possible_answer[i][j]
            std_allowed[i].append(
                standardize_string(item) if type(item) == str else item)  # noqa: E721
    return std_out in std_allowed


def _dict_checker(value: dict, possible_answers: list) -> bool:
    """Upstream ``dict_checker`` (ast_checker.py:238-298)."""
    for i in range(len(possible_answers)):
        if possible_answers[i] == "":
            continue
        possible_answer = possible_answers[i]
        flag = True
        for key, v in value.items():
            if key not in possible_answer:
                flag = False
                break
            std_v = standardize_string(v) if type(v) == str else v  # noqa: E721
            std_allowed = [
                standardize_string(a) if type(a) == str else a  # noqa: E721
                for a in possible_answer[key]]
            if std_v not in std_allowed:
                flag = False
                break
        if flag:
            for key, v in possible_answer.items():
                if key not in value and "" not in v:
                    flag = False
                    break
        if flag:
            return True
    return False


def _list_dict_checker(value: list, possible_answers: list) -> bool:
    """Upstream ``list_dict_checker`` (ast_checker.py:301-330)."""
    for answer_index in range(len(possible_answers)):
        if len(value) != len(possible_answers[answer_index]):
            continue
        flag = True
        for dict_index in range(len(value)):
            if not _dict_checker(value[dict_index],
                                 [possible_answers[answer_index][dict_index]]):
                flag = False
                break
        if flag:
            return True
    return False


def simple_function_correct(func_description: dict, model_call: dict,
                            possible_answer: dict) -> tuple[bool, str]:
    """Port of ``simple_function_checker`` (ast_checker.py:333-515), Python path.

    ``model_call`` is ``{func_name: {param: value}}``; ``possible_answer`` is
    ``{func_name: {param: [allowed, ...]}}``. Returns ``(valid, error)``."""
    pa = list(possible_answer.values())[0]
    func_name = func_description["name"]
    param_details = func_description["parameters"]["properties"]
    required_params = func_description["parameters"].get("required", [])

    if func_name not in model_call:
        return False, f"wrong_func_name: {func_name!r} not in output"
    model_params = model_call[func_name]

    for param in required_params:
        if param not in model_params:
            return False, f"missing_required: {param!r}"

    for param, value in model_params.items():
        if param not in param_details or param not in pa:
            return False, f"unexpected_param: {param!r}"

        expected_type_description = param_details[param]["type"]
        expected_type_converted = PYTHON_TYPE_MAPPING[expected_type_description]
        nested_type_converted = None
        if expected_type_description in PYTHON_NESTED_TYPE_CHECK_LIST:
            nested_type = param_details[param]["items"]["type"]
            nested_type_converted = PYTHON_TYPE_MAPPING[nested_type]

        if expected_type_description == "tuple" and type(value) == tuple:  # noqa: E721
            value = list(value)
        if expected_type_description == "float" and type(value) == int:  # noqa: E721
            value = float(value)

        valid, is_variable = _type_checker(
            value, pa[param], expected_type_converted, nested_type_converted)
        if not valid:
            return False, f"type_error: {param!r}={value!r}"

        if not is_variable:
            if expected_type_converted is dict:
                if not _dict_checker(value, pa[param]):
                    return False, f"dict_value_error: {param!r}"
                continue
            if expected_type_converted is list and nested_type_converted is dict:
                if not _list_dict_checker(value, pa[param]):
                    return False, f"list_dict_error: {param!r}"
                continue
            if expected_type_converted is str:
                if not _string_checker(value, pa[param]):
                    return False, f"string_value_error: {param!r}={value!r}"
                continue
            if expected_type_converted is list:
                if not _list_checker(value, pa[param]):
                    return False, f"list_value_error: {param!r}"
                continue

        if value not in pa[param]:
            return False, f"value_error: {param!r}={value!r}"

    for param in pa:
        if param not in model_params and "" not in pa[param]:
            return False, f"missing_optional: {param!r}"

    return True, ""
