# Agenttic console — non-negotiable design rules

These two rules are enforced *in code* in the agenttic repo
(`ui/src/components/ds/enforcement.test.tsx`). Claude Design has no constraints
input, so they travel here instead. **Any generated variant that breaks either
rule is wrong, however good it looks** — validate generated markup against the
repo's `enforcement.test.tsx`, which fails on exactly these violations.

Verbatim from `CONSOLE-DESIGN.md` §5:

## 1. Provisional is never shown as verified

A provisional score (a judged/`fi` criterion with no stored calibration record)
is a **distinct type, not a dimmer shade of a pass**. It must **never** sit on
the pass→fail colour ramp — it gets its own token (`--score-provisional`), which
reads as *withheld / unknown*, never as *slightly-passed*. A provisional
criterion scoring 0.92 renders **amber**, not green. The number is annotated
("provisional / not calibrated"), never a naked grade that scans as verified.
Calibration status is derived from the presence of a stored record, **never**
from a self-asserted `calibrated: true` flag in the payload.

## 2. A verdict colour is never emitted without its scope fence adjacent

The scope statement is a **fence, not a badge**. Wherever a verdict/status
colour is shown, the fence — untested bins, unmeasured dimensions, unexercised
assertions, provisional criteria, N/A counts — travels **with it**, physically
adjacent and equal weight, never a separate tab you can skip. There must be **no
code path that emits the verdict colour alone**: build the verdict pill and its
fence as one indivisible unit. A big pass-rate number must never lead the page.

---

Source of truth: `CONSOLE-DESIGN.md` §5 and `ui/src/components/ds/` in the
agenttic repo. The executable guard is `ui/src/components/ds/enforcement.test.tsx`.
