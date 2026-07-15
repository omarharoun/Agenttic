"""License gate — which evaluator adapters may run in which deployment.

Evaluators are third-party dependencies with licenses. Bundling or *hosting* a
source-available (Elastic v2 / SSPL / BSL) or network-copyleft (AGPL) evaluator
in a multi-tenant SaaS can violate its terms; running the same evaluator on your
own machine generally does not. So the gate is deployment-aware:

* **First-party** sources (Agenttic's own generator) always run — you are not
  "bundling a third party" when you ship your own code.
* **Permissive** (MIT / Apache-2.0 / BSD / ISC …) runs free everywhere.
* **Source-available** and **AGPL** are refused in a ``hosted`` deployment and
  allowed (relaxed) when ``self_hosted``.
* **Unknown** licenses fail closed in ``hosted`` and warn in ``self_hosted``.

Every decision is recorded so it can travel into the signed dossier — the trust
claim includes *why each source was allowed to contribute*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeploymentMode = Literal["hosted", "self_hosted"]

# SPDX classification. Kept explicit (not fuzzy-matched) so the policy is auditable.
_PERMISSIVE = frozenset({
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD",
    "Python-2.0", "PSF-2.0", "Unlicense", "Zlib", "BSL-1.0",  # Boost, not Business
})
_SOURCE_AVAILABLE = frozenset({
    "Elastic-2.0", "Elastic-License-2.0", "SSPL-1.0", "BUSL-1.1", "BSL-1.1",
    "CC-BY-NC-4.0", "PolyForm-Noncommercial-1.0.0",
})
_NETWORK_COPYLEFT = frozenset({
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
})
# Ordinary copyleft: allowed to *run* (we call it at arm's length, not link it),
# recorded but not refused. Distinguished from network copyleft on purpose.
_COPYLEFT = frozenset({
    "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0", "GPL-3.0-only",
    "GPL-3.0-or-later", "LGPL-2.1", "LGPL-3.0", "MPL-2.0",
})

Classification = Literal[
    "first_party", "permissive", "copyleft", "network_copyleft",
    "source_available", "unknown",
]

Decision = Literal["allowed", "refused"]


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict for one evaluator source (recorded in the dossier)."""

    source: str
    source_license: str
    classification: Classification
    decision: Decision
    deployment_mode: DeploymentMode
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allowed"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_license": self.source_license,
            "classification": self.classification,
            "decision": self.decision,
            "deployment_mode": self.deployment_mode,
            "reason": self.reason,
        }


def classify_license(spdx: str, *, first_party: bool = False) -> Classification:
    if first_party or spdx.startswith("LicenseRef-Agenttic"):
        return "first_party"
    if spdx in _PERMISSIVE:
        return "permissive"
    if spdx in _NETWORK_COPYLEFT:
        return "network_copyleft"
    if spdx in _SOURCE_AVAILABLE:
        return "source_available"
    if spdx in _COPYLEFT:
        return "copyleft"
    return "unknown"


def evaluate_gate(*, source: str, source_license: str, first_party: bool,
                  deployment_mode: DeploymentMode) -> GateDecision:
    """Decide whether ``source`` may run under ``deployment_mode``."""
    cls = classify_license(source_license, first_party=first_party)

    def decide(decision: Decision, reason: str) -> GateDecision:
        return GateDecision(source=source, source_license=source_license,
                            classification=cls, decision=decision,
                            deployment_mode=deployment_mode, reason=reason)

    if cls == "first_party":
        return decide("allowed", "first-party evaluator — always runnable")
    if cls == "permissive":
        return decide("allowed", f"{source_license} is permissive — runs free")
    if cls == "copyleft":
        return decide("allowed",
                      f"{source_license} copyleft — called at arm's length "
                      "(separate process/API), permitted; recorded")
    if cls in ("source_available", "network_copyleft"):
        if deployment_mode == "hosted":
            kind = ("source-available" if cls == "source_available"
                    else "network-copyleft (AGPL)")
            return decide("refused",
                          f"{source_license} is {kind}; refusing to bundle/host "
                          "it in a hosted deployment")
        return decide("allowed",
                      f"{source_license} permitted in self-hosted deployment "
                      "(relaxed); recorded")
    # unknown
    if deployment_mode == "hosted":
        return decide("refused",
                      f"unknown license {source_license!r} — failing closed in a "
                      "hosted deployment")
    return decide("allowed",
                  f"unknown license {source_license!r} — allowed in self-hosted "
                  "with a warning; recorded")
