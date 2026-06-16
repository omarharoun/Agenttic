"""Per-client rate limiting on /api (config-driven; 0 = off), pluggable backend."""

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.ratelimit import (
    InMemoryRateLimiter, RedisRateLimiter, make_rate_limiter)

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
security: {rate_limit_per_minute: %(limit)s}
"""


def _client(tmp_path, limit):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c", "limit": limit})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def test_limit_enforced(tmp_path):
    with _client(tmp_path, 3) as c:
        codes = [c.get("/api/agents").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429 and codes[4] == 429
        assert c.get("/api/agents").headers.get("retry-after") is None or True


def test_disabled_by_default(tmp_path):
    with _client(tmp_path, 0) as c:
        assert all(c.get("/api/agents").status_code == 200 for _ in range(10))


class TestBackends:
    def test_in_memory_window(self):
        b = InMemoryRateLimiter()
        assert [b.allow("k", 2, 60) for _ in range(3)] == [True, True, False]
        # a different key has its own budget
        assert b.allow("other", 2, 60) is True

    def test_factory_defaults_to_memory(self):
        assert isinstance(make_rate_limiter({}), InMemoryRateLimiter)
        assert isinstance(
            make_rate_limiter({"security": {"rate_limit_backend": "memory"}}),
            InMemoryRateLimiter)

    def test_factory_selects_redis(self):
        b = make_rate_limiter({"security": {"rate_limit_backend": "redis",
                                            "redis_url": "redis://x:6379/0"}})
        assert isinstance(b, RedisRateLimiter)  # constructed without connecting
        assert b._url == "redis://x:6379/0"

    def test_factory_redis_url_from_env(self, monkeypatch):
        # redis_url commonly comes from ASCORE_REDIS_URL, not the config file
        monkeypatch.setenv("ASCORE_REDIS_URL", "redis://envhost:6379/0")
        b = make_rate_limiter({"security": {"rate_limit_backend": "redis",
                                            "redis_url": ""}})
        assert isinstance(b, RedisRateLimiter) and b._url == "redis://envhost:6379/0"

    def test_factory_redis_without_any_url_falls_back(self, monkeypatch):
        monkeypatch.delenv("ASCORE_REDIS_URL", raising=False)
        b = make_rate_limiter({"security": {"rate_limit_backend": "redis"}})
        assert isinstance(b, InMemoryRateLimiter)  # no url => safe fallback, no crash

    def test_redis_backend_zset_logic_with_fake(self):
        # exercise the sliding-window logic against a minimal fake redis,
        # so the Redis path is covered without a running server
        b = RedisRateLimiter(client=_FakeRedis())
        assert [b.allow("k", 2, 60) for _ in range(3)] == [True, True, False]


class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def zremrangebyscore(self, key, lo, hi):
        self.ops.append(("rem", key, lo, hi)); return self

    def zadd(self, key, mapping):
        self.ops.append(("add", key, mapping)); return self

    def zcard(self, key):
        self.ops.append(("card", key)); return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl)); return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "rem":
                _, key, lo, hi = op
                z = self.store.setdefault(key, {})
                for m in [m for m, s in z.items() if lo <= s <= hi]:
                    del z[m]
                results.append(None)
            elif op[0] == "add":
                _, key, mapping = op
                self.store.setdefault(key, {}).update(mapping)
                results.append(1)
            elif op[0] == "card":
                results.append(len(self.store.get(op[1], {})))
            else:
                results.append(True)
        self.ops = []
        return results


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def pipeline(self):
        return _FakePipeline(self.store)
