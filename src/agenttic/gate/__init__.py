"""Receipt-gated tool access (verification spec §5, in-process decorator).

The token layer lives in :mod:`agenttic.gate.receipt`: a Tool Access Receipt is
a short-lived, single-use capability token a third party's tool can verify
offline against the published JWKS, so it refuses any call that doesn't carry a
valid, current, action-matched receipt.
"""

from agenttic.gate.receipt import (
    DEFAULT_TTL_SECONDS,
    IRREVERSIBLE_TTL_SECONDS,
    TYP,
    ActionClass,
    Principal,
    ToolAccessReceipt,
    compute_action_hash,
    compute_bound_params,
    issue_tool_access_receipt,
    new_nonce,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "IRREVERSIBLE_TTL_SECONDS",
    "TYP",
    "ActionClass",
    "Principal",
    "ToolAccessReceipt",
    "compute_action_hash",
    "compute_bound_params",
    "issue_tool_access_receipt",
    "new_nonce",
]
