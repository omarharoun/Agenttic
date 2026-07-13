"""Certification track package.

SPEC-1 shipped a single ``certification.py`` module (safety-cert grading + Ed25519
signing). SPEC-2 → SPEC-6 grow this into a package (profiles, tiers, dossiers,
elicitation, hashing, staleness, …). To keep every existing importer working, the
original module lives on as :mod:`agenttic.certification.safety_cert` and all of its
public *and* private names are re-exported here, so ``from agenttic import
certification as cert; cert.<anything>`` continues to resolve exactly as before.
"""

from . import safety_cert as _safety_cert

# Re-export the full legacy namespace (including internal helpers used as
# ``cert._x`` by existing callers) so this package is a drop-in replacement for
# the old flat module.
globals().update(
    {k: v for k, v in vars(_safety_cert).items() if not k.startswith("__")}
)

del _safety_cert
