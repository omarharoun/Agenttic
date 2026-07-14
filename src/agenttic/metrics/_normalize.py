"""Unicode normalization for safety comparisons (scorer-integrity fix R1).

Every safety substring / regex comparison in the scorer used a bare ``.lower()``.
That is trivially bypassed with Unicode: a fullwidth danger marker
(``ｄｅｌｅｔｅｄ``) or a secret split by zero-width spaces
(``s​k-live-…``) does not literally equal its ASCII form, so a check that
`.lower()`-compares scores it 1.0 SAFE while the unsafe content is plainly there.

``normalize`` collapses those evasions before comparison:

* **NFKC** — Unicode compatibility fold, so fullwidth / homoglyph / stylized
  characters map to their canonical ASCII form (``ｄｅｌｅｔｅ`` -> ``delete``).
* **strip zero-width + control / format chars** — every character whose Unicode
  general category starts with ``"C"`` (Cc control, Cf format incl. zero-width
  space/joiner, Cs surrogate, Co private-use, Cn unassigned) is removed, so a
  payload broken up by invisible characters rejoins into the string it hides.
* **casefold** — aggressive, locale-independent lowercasing (stronger than
  ``.lower()``: ``ß`` -> ``ss``) so the comparison is case-insensitive.

It is idempotent (``normalize(normalize(x)) == normalize(x)``), so double-routing
a value through it is harmless.

HONEST LIMIT: normalization closes character-level obfuscation (homoglyphs,
fullwidth, zero-width, casing). It does NOT defeat semantic evasion (a paraphrase,
a translation, a novel synonym) — that is the detector layers' job, not this
function's. Stripping control chars also removes newlines/tabs, which merges
tokens across line breaks; that is intentional for substring detection (an
attacker cannot hide a marker by inserting a newline) and callers that need
sentence structure (e.g. first-clause refusal anchoring) must split BEFORE
normalizing.
"""

from __future__ import annotations

import unicodedata


def strip_invisible(text: str) -> str:
    """NFKC-fold and strip zero-width/control chars, PRESERVING case.

    This is the case-preserving half of :func:`normalize`. Use it to feed the
    value-independent, case-SENSITIVE regex detectors (secret/PII patterns such as
    AWS ``AKIA…`` or a PEM ``BEGIN … PRIVATE KEY`` header) — it removes the
    zero-width spaces / fullwidth digits an attacker uses to break a token apart,
    so the pattern matches, WITHOUT casefolding (which would lowercase the token
    and defeat those uppercase-anchored patterns)."""
    if not text:
        return ""
    # NFKC first so compatibility characters (fullwidth, homoglyph, stylized)
    # collapse to canonical form before we strip.
    folded = unicodedata.normalize("NFKC", text)
    # Drop every control / format / zero-width character (category starts "C").
    return "".join(
        ch for ch in folded if not unicodedata.category(ch).startswith("C"))


def normalize(text: str) -> str:
    """NFKC-fold, strip zero-width/control chars, and casefold ``text``.

    Route EVERY safety SUBSTRING comparison through this instead of a bare
    ``.lower()`` so character-level Unicode obfuscation cannot bypass a check.
    For case-sensitive regex detectors use :func:`strip_invisible` instead.
    Returns ``""`` for a falsy/empty input."""
    return strip_invisible(text).casefold()
