#!/usr/bin/env python
"""Generate docs/cert-swe-v1/COVERAGE.md from the live cert-swe-v1 profile.

Seeds the pack suites into a throwaway registry, builds the manifest from the
actual config + registry state, and writes the coverage/provenance table. Run
from the repo root:  python scripts/gen_swe_manifest.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agenttic.certification.swe_pack import PROFILE_ID, pack_manifest, render_markdown
from agenttic.config import load_config
from agenttic.metrics.standard_suites import seed_standard_suites
from agenttic.metrics.swe_suites import seed_swe_suites
from agenttic.registry.sqlite_store import Registry

OUT = Path("docs/cert-swe-v1/COVERAGE.md")


def main() -> None:
    cfg = load_config("config.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/manifest.db")
        seed_standard_suites(reg)
        seed_swe_suites(reg)
        manifest = pack_manifest(cfg, reg, PROFILE_ID)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_markdown(manifest))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
