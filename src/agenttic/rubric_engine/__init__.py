"""The Rubric Engine (SPEC-9) — classify any agent into an archetype, synthesize
its rubric as mostly-reused proven criteria plus a small audited delta, and
refuse to ship it until it provably discriminates good agents from bad.

Public surface:
  * ``cores``        — the six authored seed archetypes + their core rubrics.
  * ``taxonomy``     — the archetype tree + inheritance resolution.
  * ``ethos``        — the cross-cutting ETHOS safety overlay.
  * ``classify``     — automatic archetype classification (Step 40).
  * ``synthesize``   — core + domain-delta rubric synthesis (Step 41).
  * ``discrimination`` — the fit gate: does the rubric separate good from bad (Step 42).
  * ``library``      — the compounding four-source rubric library (Step 43).
  * ``evaluate``     — the one-call operator flow (Step 44).
"""
