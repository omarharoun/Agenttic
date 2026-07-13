"""ascore → agenttic env-var back-compat shim (:mod:`agenttic._env`).

The rename must NEVER stop honoring the legacy ``ASCORE_*`` names: node1's
production ``.env`` supplies ``ASCORE_CERT_SIGNING_KEY`` and
``ASCORE_PASSPORT_SIGNING_KEY``, and if the renamed code stopped reading them
cert/passport signing would fail closed and the app would 502. These tests pin:

* precedence: ``AGENTTIC_<X>`` wins over ``ASCORE_<X>``;
* fallback: a lone ``ASCORE_<X>`` is still read, with a ``DeprecationWarning``;
* default when neither is set; non-shimmed names pass through verbatim;
* ``get_secret`` applies the same shim (incl. the ``*_FILE`` convention);
* INTEGRATION: the cert + passport signing keys actually load from the legacy
  ``ASCORE_*`` names (the deploy canary), and ``AGENTTIC_*`` overrides them.
"""

from __future__ import annotations

import warnings

import pytest

from agenttic._env import candidate_names, get_env


def test_candidate_names_maps_prefixes():
    assert candidate_names("ASCORE_CERT_SIGNING_KEY") == (
        "AGENTTIC_CERT_SIGNING_KEY", "ASCORE_CERT_SIGNING_KEY")
    assert candidate_names("AGENTTIC_DB") == ("AGENTTIC_DB", "ASCORE_DB")
    # non-shimmed name is returned verbatim, no legacy twin
    assert candidate_names("ANTHROPIC_API_KEY") == ("ANTHROPIC_API_KEY", None)


def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("AGENTTIC_DB", "new")
    monkeypatch.setenv("ASCORE_DB", "old")
    assert get_env("ASCORE_DB") == "new"
    assert get_env("AGENTTIC_DB") == "new"  # querying either spelling agrees


def test_legacy_fallback_emits_deprecation_warning(monkeypatch):
    monkeypatch.delenv("AGENTTIC_DB", raising=False)
    monkeypatch.setenv("ASCORE_DB", "legacy")
    with pytest.warns(DeprecationWarning, match="ASCORE_DB is deprecated"):
        assert get_env("ASCORE_DB") == "legacy"


def test_new_name_does_not_warn(monkeypatch, recwarn):
    monkeypatch.setenv("AGENTTIC_DB", "new")
    monkeypatch.delenv("ASCORE_DB", raising=False)
    assert get_env("ASCORE_DB") == "new"
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("AGENTTIC_DB", raising=False)
    monkeypatch.delenv("ASCORE_DB", raising=False)
    assert get_env("ASCORE_DB") is None
    assert get_env("ASCORE_DB", "fallback") == "fallback"


def test_non_shimmed_name_passes_through(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a passthrough must NOT warn
        assert get_env("ANTHROPIC_API_KEY") == "k"


def test_get_env_respects_explicit_environ_mapping():
    assert get_env("ASCORE_DB", environ={"AGENTTIC_DB": "x"}) == "x"
    with pytest.warns(DeprecationWarning):
        assert get_env("ASCORE_DB", environ={"ASCORE_DB": "y"}) == "y"


# --- get_secret shim (+ *_FILE convention) ---------------------------------

def test_get_secret_prefers_new_name(monkeypatch):
    from agenttic.secrets import get_secret
    monkeypatch.setenv("AGENTTIC_API_TOKEN", "new-tok")
    monkeypatch.setenv("ASCORE_API_TOKEN", "old-tok")
    assert get_secret("ASCORE_API_TOKEN") == "new-tok"


def test_get_secret_legacy_fallback_warns(monkeypatch):
    from agenttic.secrets import get_secret
    monkeypatch.delenv("AGENTTIC_API_TOKEN", raising=False)
    monkeypatch.setenv("ASCORE_API_TOKEN", "old-tok")
    with pytest.warns(DeprecationWarning):
        assert get_secret("ASCORE_API_TOKEN") == "old-tok"


def test_get_secret_file_convention_new_name(monkeypatch, tmp_path):
    from agenttic.secrets import get_secret
    f = tmp_path / "tok"
    f.write_text("  filed-secret\n")
    monkeypatch.delenv("AGENTTIC_API_TOKEN", raising=False)
    monkeypatch.delenv("ASCORE_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTTIC_API_TOKEN_FILE", str(f))
    assert get_secret("ASCORE_API_TOKEN") == "filed-secret"


def test_get_secret_legacy_file_convention_still_honored(monkeypatch, tmp_path):
    from agenttic.secrets import get_secret
    f = tmp_path / "tok"
    f.write_text("legacy-filed\n")
    monkeypatch.delenv("AGENTTIC_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENTTIC_API_TOKEN_FILE", raising=False)
    monkeypatch.delenv("ASCORE_API_TOKEN", raising=False)
    monkeypatch.setenv("ASCORE_API_TOKEN_FILE", str(f))
    with pytest.warns(DeprecationWarning):
        assert get_secret("ASCORE_API_TOKEN") == "legacy-filed"


# --- INTEGRATION: the production signing keys (the deploy canary) -----------

def _isolate_signing_env(monkeypatch):
    for n in ("AGENTTIC_PASSPORT_SIGNING_KEY", "ASCORE_PASSPORT_SIGNING_KEY",
              "AGENTTIC_CERT_SIGNING_KEY", "ASCORE_CERT_SIGNING_KEY"):
        monkeypatch.delenv(n, raising=False)


def test_passport_signing_key_still_read_from_legacy_ascore(monkeypatch):
    """node1 sets only ASCORE_PASSPORT_SIGNING_KEY — signing must still load it."""
    from agenttic.passport import keys as pk
    _isolate_signing_env(monkeypatch)
    seed = pk.private_seed_b64(pk.generate_key())
    monkeypatch.setenv("ASCORE_PASSPORT_SIGNING_KEY", seed)
    with pytest.warns(DeprecationWarning):
        loaded = pk._load_from_env()
    assert loaded is not None
    # the loaded key is exactly the one whose seed we set (kid is deterministic)
    assert pk.key_id(loaded.public_key()) == pk.key_id(
        pk.load_private_from_seed(seed).public_key())


def test_passport_new_name_overrides_legacy(monkeypatch):
    from agenttic.passport import keys as pk
    _isolate_signing_env(monkeypatch)
    new_seed = pk.private_seed_b64(pk.generate_key())
    old_seed = pk.private_seed_b64(pk.generate_key())
    monkeypatch.setenv("AGENTTIC_PASSPORT_SIGNING_KEY", new_seed)
    monkeypatch.setenv("ASCORE_PASSPORT_SIGNING_KEY", old_seed)
    loaded = pk._load_from_env()
    assert pk.key_id(loaded.public_key()) == pk.key_id(
        pk.load_private_from_seed(new_seed).public_key())


def test_cert_signing_key_still_read_from_legacy_ascore(monkeypatch):
    """node1 sets only ASCORE_CERT_SIGNING_KEY — issuance must still load it."""
    from agenttic.certification import safety_cert as sc
    from agenttic.passport import keys as pk
    _isolate_signing_env(monkeypatch)
    seed = pk.private_seed_b64(pk.generate_key())  # base64 of a 32-byte seed
    monkeypatch.setenv("ASCORE_CERT_SIGNING_KEY", seed)
    with pytest.warns(DeprecationWarning):
        key = sc.signing_key(cfg=None)
    # matches the configured material, NOT the publicly-known dev key
    assert sc.key_id(key.public_key()) == sc.key_id(
        pk.load_private_from_seed(seed).public_key())
    dev = sc.signing_key(cfg={"env": "development"})  # unset → dev key
    _isolate_signing_env(monkeypatch)
    assert sc.key_id(key.public_key()) != sc.key_id(
        sc.signing_key(cfg={"env": "development"}).public_key())
