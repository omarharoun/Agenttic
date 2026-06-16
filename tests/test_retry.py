"""Retry policy: transient errors retried with backoff; client errors are not."""

import pytest

from ascore.retry import RetryPolicy, is_retryable, with_retry


class _ApiErr(Exception):
    """Mimics an anthropic APIStatusError (has .status_code)."""
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


class _Named(Exception):
    """Mimics SDK exceptions classified by class name."""


class APITimeoutError(_Named): pass
class InternalServerError(_Named): pass
class BadRequestError(_Named):
    status_code = 400


class TestClassification:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 529, 408, 409])
    def test_retryable_status(self, status):
        assert is_retryable(_ApiErr(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_nonretryable_status(self, status):
        assert is_retryable(_ApiErr(status)) is False

    def test_retryable_by_name(self):
        assert is_retryable(APITimeoutError()) and is_retryable(InternalServerError())

    def test_connection_and_timeout(self):
        assert is_retryable(ConnectionError()) and is_retryable(TimeoutError())

    def test_plain_error_not_retryable(self):
        assert is_retryable(ValueError("nope")) is False


class TestWithRetry:
    def test_transient_then_success(self):
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise InternalServerError("upstream 500")
            return "ok"
        slept = []
        out = with_retry(fn, RetryPolicy(max_attempts=5, base_delay=0.01, jitter=False),
                         sleep=slept.append)
        assert out == "ok" and calls["n"] == 3 and len(slept) == 2

    def test_exhausts_and_reraises(self):
        def fn():
            raise InternalServerError("always 500")
        with pytest.raises(InternalServerError):
            with_retry(fn, RetryPolicy(max_attempts=3, base_delay=0, jitter=False),
                       sleep=lambda _: None)

    def test_nonretryable_not_retried(self):
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            raise BadRequestError("bad input")
        with pytest.raises(BadRequestError):
            with_retry(fn, RetryPolicy(max_attempts=5), sleep=lambda _: None)
        assert calls["n"] == 1  # never retried

    def test_backoff_grows(self):
        delays = []
        def fn():
            raise InternalServerError("500")
        with pytest.raises(InternalServerError):
            with_retry(fn, RetryPolicy(max_attempts=4, base_delay=1.0, jitter=False),
                       sleep=delays.append)
        assert delays == [1.0, 2.0, 4.0]  # exponential

    def test_from_cfg(self):
        p = RetryPolicy.from_cfg({"anthropic": {"retry": {"max_attempts": 7,
                                                          "base_delay": 0.25}}})
        assert p.max_attempts == 7 and p.base_delay == 0.25
