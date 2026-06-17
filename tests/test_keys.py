"""Per-tenant Anthropic key: encryption at rest, masking, no-leak, client wiring."""

from ascore.registry.sqlite_store import ApiKeyRow, Registry
from ascore.server.crypto import decrypt, encrypt
from ascore.server.keys import KeyStore, build_tenant_clients, mask
from sqlmodel import Session, select

CFG = {"auth": {"session_secret": "test-secret"}}


class TestCrypto:
    def test_roundtrip(self):
        ct = encrypt(CFG, "sk-ant-secret-123")
        assert ct != "sk-ant-secret-123"
        assert decrypt(CFG, ct) == "sk-ant-secret-123"

    def test_wrong_secret_returns_none(self):
        ct = encrypt(CFG, "sk-ant-secret-123")
        assert decrypt({"auth": {"session_secret": "different"}}, ct) is None


class TestKeyStore:
    def test_set_get_status_mask(self, tmp_path):
        ks = KeyStore(Registry(tmp_path / "k.db").engine, CFG)
        ks.set_key("acme", "sk-ant-aaaaaaaaaaaa9999")
        assert ks.get_key("acme") == "sk-ant-aaaaaaaaaaaa9999"
        st = ks.status("acme")
        assert st["set"] is True and st["masked"] == "sk-ant-…9999"
        # the raw key must never appear in the safe status
        assert "aaaaaaaa" not in str(st)

    def test_stored_ciphertext_is_encrypted(self, tmp_path):
        eng = Registry(tmp_path / "k.db").engine
        KeyStore(eng, CFG).set_key("acme", "sk-ant-plaintextkey-7777")
        with Session(eng) as s:
            row = s.exec(select(ApiKeyRow).where(ApiKeyRow.tenant_id == "acme")).first()
        assert "plaintextkey" not in row.ciphertext  # not stored in the clear
        assert row.last4 == "7777"

    def test_missing_and_delete(self, tmp_path):
        ks = KeyStore(Registry(tmp_path / "k.db").engine, CFG)
        assert ks.get_key("none") is None
        assert ks.status("none")["set"] is False
        ks.set_key("acme", "sk-ant-xxxxxxxxxxxx1234")
        assert ks.delete("acme") is True and ks.get_key("acme") is None
        assert ks.delete("acme") is False

    def test_per_tenant_isolation(self, tmp_path):
        ks = KeyStore(Registry(tmp_path / "k.db").engine, CFG)
        ks.set_key("a", "sk-ant-aaaaaaaaaaaa1111")
        ks.set_key("b", "sk-ant-bbbbbbbbbbbb2222")
        assert ks.get_key("a").endswith("1111")
        assert ks.get_key("b").endswith("2222")


class TestClientWiring:
    def test_build_tenant_clients_uses_key(self):
        clients = build_tenant_clients("sk-ant-test-key-9999")
        assert set(clients) == {"agent", "judge", "generator", "anthropic"}
        for c in clients.values():
            assert c.api_key == "sk-ant-test-key-9999"

    def test_mask(self):
        assert mask("abcd") == "sk-ant-…abcd"
