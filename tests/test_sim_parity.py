"""SPEC-5 Step 22 — golden parity, Python side.

The committed fixtures under fixtures/sim-parity/ are the contract the TypeScript
sim-core is proven against (ui/src/sim-core/parity.test.ts). This test regenerates
them in-memory from the REAL engine and asserts they still match what is checked
in. So the parity gate has two teeth:

  * here: a change to the Python decision math that alters any output fails until
    the fixtures are regenerated (`.venv/bin/python scripts/gen_sim_parity.py`);
  * there: the TS port must then reproduce the regenerated fixtures.

Neither side can drift silently.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "sim-parity"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_sim_parity", ROOT / "scripts" / "gen_sim_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_fixtures_match_engine():
    gen = _load_generator()
    fresh = gen.build_all()
    # round-trip through JSON so tuples/float reprs normalise exactly as written
    fresh = json.loads(json.dumps(fresh))
    for name, cases in fresh.items():
        committed = json.loads((FIXTURES / f"{name}.json").read_text())
        assert committed == cases, (
            f"fixtures/sim-parity/{name}.json is stale — regenerate with "
            f"`.venv/bin/python scripts/gen_sim_parity.py` (engine output changed)"
        )


def test_moat_branches_are_covered():
    """The instructive gate verdicts SPEC-5 leans on must all appear, so the TS
    parity replay actually exercises them (esp. the fail-closed 'lobotomy')."""
    gate = json.loads((FIXTURES / "gate.json").read_text())
    reasons = [c["expected"]["reason"] for c in gate]
    assert any("missing baseline criteria" in r for r in reasons)      # lobotomy
    assert any("dropped beyond epsilon" in r for r in reasons)         # sneaky
    assert any("would significantly regress" in r for r in reasons)    # sig veto
    assert any(c["expected"]["promote"] for c in gate)                 # clean win
