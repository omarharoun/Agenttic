"""SSRF validation for black-box agent URLs."""

import pytest

from agenttic.security import UnsafeURLError, validate_blackbox_url


class TestSchemeAndHost:
    def test_https_public_host_ok(self):
        # resolve disabled so the test never depends on real DNS
        assert validate_blackbox_url("https://api.example.com/agent",
                                     resolve=False)

    def test_non_http_scheme_blocked(self):
        for url in ("file:///etc/passwd", "gopher://x/", "ftp://h/"):
            with pytest.raises(UnsafeURLError):
                validate_blackbox_url(url)

    def test_no_host_blocked(self):
        with pytest.raises(UnsafeURLError):
            validate_blackbox_url("http:///nohost")


class TestPrivateRanges:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1:8080/",                     # loopback
        "http://10.0.0.5/",                           # private
        "http://192.168.1.10/agent",                  # private
        "http://[::1]/",                              # loopback v6
        "http://0.0.0.0/",                            # unspecified
    ])
    def test_literal_private_ip_blocked(self, url):
        with pytest.raises(UnsafeURLError):
            validate_blackbox_url(url)

    def test_localhost_resolves_and_is_blocked(self):
        with pytest.raises(UnsafeURLError):
            validate_blackbox_url("http://localhost:9000/agent", resolve=True,
                                  allow_unresolved=False)

    def test_block_private_can_be_disabled(self):
        cfg = {"security": {"blackbox_block_private": False}}
        assert validate_blackbox_url("http://10.0.0.5/", cfg=cfg)


class TestAllowlist:
    def test_allowlist_rejects_others(self):
        cfg = {"security": {"blackbox_url_allowlist": ["agents.acme.com"]}}
        assert validate_blackbox_url("https://agents.acme.com/x", cfg=cfg,
                                     resolve=False)
        assert validate_blackbox_url("https://api.agents.acme.com/x", cfg=cfg,
                                     resolve=False)
        with pytest.raises(UnsafeURLError):
            validate_blackbox_url("https://evil.com/x", cfg=cfg, resolve=False)


class TestUnresolved:
    def test_unresolved_allowed_at_registration(self):
        # a not-yet-deployed endpoint registers fine
        assert validate_blackbox_url("https://not-deployed-xyz.internalcorp/x",
                                     allow_unresolved=True)
