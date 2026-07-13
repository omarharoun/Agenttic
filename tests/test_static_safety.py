"""Path-traversal containment for the SPA static fallback."""

from agenttic.server.app import safe_static_path


def test_serves_real_file_inside_dist(tmp_path):
    (tmp_path / "assets").mkdir()
    f = tmp_path / "assets" / "app.js"
    f.write_text("ok")
    assert safe_static_path(tmp_path, "assets/app.js") == f.resolve()


def test_traversal_escapes_are_refused(tmp_path):
    (tmp_path / "index.html").write_text("spa")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")
    for attack in ("../secret.txt", "../../etc/passwd",
                   "assets/../../secret.txt", "/etc/passwd"):
        assert safe_static_path(tmp_path, attack) is None, attack


def test_missing_file_returns_none(tmp_path):
    assert safe_static_path(tmp_path, "nope.js") is None


def test_dist_root_itself_is_not_a_file(tmp_path):
    assert safe_static_path(tmp_path, "") is None
