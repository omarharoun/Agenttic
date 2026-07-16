"""Path-traversal containment for the SPA static fallback."""

from agenttic.server.app import (
    _empty_root_div,
    clean_shell,
    prerendered_page,
    safe_static_path,
)


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


# --- prerendered_page: request path -> <route>.html mapping ------------------

def _build_dist(tmp_path):
    (tmp_path / "index.html").write_text("INDEX")
    (tmp_path / "pricing.html").write_text("PRICING")
    return tmp_path


def test_prerendered_root_maps_to_index(tmp_path):
    d = _build_dist(tmp_path)
    assert prerendered_page(d, "") == (d / "index.html").resolve()


def test_prerendered_named_page(tmp_path):
    d = _build_dist(tmp_path)
    assert prerendered_page(d, "pricing") == (d / "pricing.html").resolve()
    # trailing slash normalises to the same page
    assert prerendered_page(d, "pricing/") == (d / "pricing.html").resolve()


def test_prerendered_missing_page_is_none(tmp_path):
    d = _build_dist(tmp_path)
    assert prerendered_page(d, "scan") is None          # not prerendered
    assert prerendered_page(d, "certified/abc") is None  # dynamic / nested
    assert prerendered_page(d, "app/build") is None


def test_prerendered_never_traverses(tmp_path):
    d = _build_dist(tmp_path)
    (tmp_path.parent / "secret.html").write_text("secret")
    for attack in ("..", "../secret", "../../etc/passwd"):
        assert prerendered_page(d, attack) is None, attack


# --- _empty_root_div: balanced strip of #root contents -----------------------

def test_empty_root_strips_nested_markup():
    html = ('<body><div id="root" data-server-rendered="true">'
            '<div class="a"><div class="b">hi</div></div></div>'
            '<script>x</script></body>')
    out = _empty_root_div(html)
    assert 'class="a"' not in out and "hi" not in out
    assert '<div id="root" data-server-rendered="true"></div>' in out
    assert "<script>x</script>" in out  # content after root is preserved


def test_empty_root_noop_without_root():
    html = "<body><p>no root here</p></body>"
    assert _empty_root_div(html) == html


# --- clean_shell: bare, un-prerendered SPA shell -----------------------------

def test_clean_shell_removes_marker_and_empties_root(tmp_path):
    (tmp_path / "index.html").write_text(
        '<!DOCTYPE html><html><head>'
        '<script type="module" src="/assets/app-x.js"></script></head>'
        '<body><div id="root" data-server-rendered="true">'
        '<header>LANDING</header></div></body></html>')
    shell = clean_shell(tmp_path)
    assert shell is not None
    assert 'data-server-rendered="true"' not in shell   # → client render()
    assert "LANDING" not in shell                        # no prerendered body
    assert '<div id="root"></div>' in shell              # empty mount
    assert "/assets/app-x.js" in shell                   # build tags preserved


def test_clean_shell_none_without_build(tmp_path):
    assert clean_shell(tmp_path) is None
