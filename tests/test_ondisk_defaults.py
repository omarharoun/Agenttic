"""On-disk default filename after the ascore → agenttic rename.

New installs use ``agenttic.db``; but an existing legacy ``ascore.db`` in the
working directory is still opened (no data loss). ``agenttic.db`` wins whenever
present.
"""

from __future__ import annotations

from agenttic.registry.sqlite_store import Registry, default_db_filename
from agenttic.schema.testcase import TestCase, TestSuite


def _seed(reg: Registry) -> None:
    cs = [TestCase(test_id="tc-0", suite_id="s-1", version=1,
                   task_description="t", input={"q": "q0"}, rubric_id="r-1")]
    reg.save_suite(TestSuite(suite_id="s-1", version=1, business_context="ctx",
                             test_ids=["tc-0"], approved=False), cs)


def test_new_install_prefers_agenttic_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert default_db_filename() == "agenttic.db"


def test_falls_back_to_existing_ascore_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ascore.db").write_bytes(b"")  # legacy registry file present
    assert default_db_filename() == "ascore.db"


def test_agenttic_db_wins_when_both_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ascore.db").write_bytes(b"")
    (tmp_path / "agenttic.db").write_bytes(b"")
    assert default_db_filename() == "agenttic.db"


def test_registry_new_install_creates_agenttic_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Registry()  # no db_path/url → default filename
    assert (tmp_path / "agenttic.db").exists()
    assert not (tmp_path / "ascore.db").exists()


def test_registry_reuses_existing_ascore_db_no_data_loss(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # a legacy deployment already has data in ascore.db
    _seed(Registry(db_path="ascore.db"))
    assert (tmp_path / "ascore.db").exists()

    # a fresh default Registry must reopen ascore.db, see the data, and NOT
    # silently start a new empty agenttic.db (which would orphan the registry)
    assert default_db_filename() == "ascore.db"
    r = Registry()
    assert [s["suite_id"] for s in r.list_suites()] == ["s-1"]
    assert not (tmp_path / "agenttic.db").exists()
