"""Interoperability layer — losslessly bridge agenttic's evaluation records to
and from external eval harnesses so others can re-run our evals in tooling they
already trust.

Currently ships the **Inspect** bridge (UK AI Safety Institute's ``inspect_ai``
``EvalLog`` format). See :mod:`ascore.interop.inspect_log`.
"""

from ascore.interop.inspect_log import (
    INTEROP_VERSION,
    from_inspect_log,
    to_inspect_log,
)

__all__ = ["INTEROP_VERSION", "to_inspect_log", "from_inspect_log"]
