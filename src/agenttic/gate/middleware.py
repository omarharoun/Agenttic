"""Receipt verification pipeline + the ``@require_receipt`` FastAPI decorator
(RECEIPT-SCHEMA.md §3, §8 — verification spec §5, in-process variant).

The check lives in the *tool's* process, not in the agent's honesty: strip the
SDK, remove the gateway from the path, replay a leaked receipt — the endpoint
still refuses, because every path here fails closed.

Order is load-bearing, not stylistic (§3): cheap offline checks first, the
possibly-networked revocation check next, the **stateful** nonce claim last, so
a receipt that was going to fail never burns its nonce or triggers a round-trip.

What this proves is bounded (§6): the call was governed by a named, scoped,
signed decision. Not that it was safe.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from fastapi import HTTPException, Request

from agenttic.gate.receipt import (
    TYP,
    ActionClass,
    ToolAccessReceipt,
    compute_action_hash,
    compute_bound_params,
)
from agenttic.verifier.header import decode_passport_header, encode_passport_header
from agenttic.verifier.sdk import (
    ExpiredError,
    RevokedError,
    TamperedError,
    UnknownKeyError,
    VerifyError,
    check_status,
    verify_receipt,
)

# Same convention as ``Agent-Passport``, same base64-of-JSON codec.
HEADER_NAME = "Agent-Tool-Receipt"
encode_receipt_header = encode_passport_header
decode_receipt_header = decode_passport_header

# §2.4 calibration knobs: the gateway host is not the tool host, so the clocks
# differ; and revocation staleness is a deliberate, bounded trade.
DEFAULT_SKEW_SECONDS = 5.0
REVOCATION_TTL_SECONDS = 60.0

# The Request parameter added to an endpoint that didn't ask for one.
_INJECTED_REQUEST = "__receipt_request"


# --------------------------------------------------------------------------- #
# Errors — reuse the verifier hierarchy; add only what is genuinely new.
# --------------------------------------------------------------------------- #


class ActionMismatchError(VerifyError):
    """The receipt does not authorise this action *shape* — or, for an
    irreversible action, this *instance* (§4 substitution, not replay)."""


class ReplayError(VerifyError):
    """The nonce was already claimed. A receipt is single-use."""


# --------------------------------------------------------------------------- #
# Nonce store.
# --------------------------------------------------------------------------- #


class NonceStore(Protocol):
    def claim(self, nonce: str, expires_at: datetime) -> bool:
        """Claim a nonce for its one use. ``True`` = first use, ``False`` =
        replay. Must be atomic: claim-by-insert, never check-then-insert."""


class InMemoryNonceStore:
    """Single-process store — for tests and for embedding in one process.

    Not the default: two workers each hold their own dict, so the same receipt
    replays once per worker and an irreversible action executes twice. Use it
    only where there is genuinely one process; otherwise :class:`FileNonceStore`.
    """

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._seen: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._now = now

    def claim(self, nonce: str, expires_at: datetime) -> bool:
        with self._lock:
            # Claim-by-insert: the membership test and the insert are ONE
            # critical section. Split them and two concurrent replays both walk
            # through the TOCTOU window between them.
            self._prune()
            if nonce in self._seen:
                return False
            self._seen[nonce] = _utc(expires_at)
            return True

    def _prune(self) -> None:
        # Bounded by issuance-rate × TTL (§2.4): past expiry a replay is already
        # rejected at step 2, so forgetting the nonce costs nothing.
        now = _clock(self._now)
        for nonce in [n for n, exp in self._seen.items() if exp <= now]:
            del self._seen[nonce]


# A shared directory, so every worker on the host contends for the same create.
# The env var is the deployment knob: workers in separate containers need it
# pointed at one mounted volume, or each container's /tmp is its own island.
# It must be a directory only this tool's uid can write — on a shared host,
# anyone who can unlink a claim file can replay the receipt it was holding.
DEFAULT_NONCE_DIR = os.environ.get(
    "AGENTTIC_NONCE_DIR", os.path.join(tempfile.gettempdir(), "agenttic-nonces"))


class FileNonceStore:
    """Default store for a tool with no Agenttic registry of its own.

    Claim-by-create: ``O_CREAT|O_EXCL`` is atomic across processes, so the
    membership test and the insert are one operation the kernel arbitrates —
    the same guarantee ``UniqueConstraint`` gives, without a database. A dict
    cannot do this: worker B's dict has never seen worker A's nonce, so a
    replay across workers wins and an irreversible action runs twice.

    # ponytail: one host — a registry-backed UniqueConstraint store
    # (RECEIPT-SCHEMA.md §7) lifts it to many.
    """

    def __init__(self, directory: str = DEFAULT_NONCE_DIR, *,
                 now: Callable[[], datetime] | None = None) -> None:
        self._dir = directory
        self._now = now

    def claim(self, nonce: str, expires_at: datetime) -> bool:
        os.makedirs(self._dir, mode=0o700, exist_ok=True)  # lazy: no import-time write
        self._prune()
        # Hashed, never interpolated raw: the nonce is attacker-shaped input and
        # a filename is a path.
        path = os.path.join(self._dir, hashlib.sha256(nonce.encode()).hexdigest())
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(_utc(expires_at).isoformat())
        return True

    def _prune(self) -> None:
        # Same bound as the in-memory store (§2.4): past expiry a replay is
        # already rejected at step 2, so forgetting the nonce costs nothing.
        now = _clock(self._now)
        for name in os.listdir(self._dir):
            path = os.path.join(self._dir, name)
            try:
                with open(path) as fh:
                    expired = datetime.fromisoformat(fh.read()) <= now
            except (OSError, ValueError):
                continue  # half-written or already gone: keep it, fail closed
            if expired:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# --------------------------------------------------------------------------- #
# Revocation (per-passport — there is no receipt CRL, §0.5).
# --------------------------------------------------------------------------- #


class RevocationCache:
    """passport_id → (status, fetched_at), TTL-bounded.

    ``fetcher(status_url) -> dict`` is the injectable status fetcher, passed
    straight through to :func:`agenttic.verifier.sdk.check_status`.
    """

    def __init__(self, *, fetcher: Callable[[str], dict] | None = None,
                 status_url_base: str = "https://agenttic.local",
                 ttl_seconds: float = REVOCATION_TTL_SECONDS,
                 now: Callable[[], datetime] | None = None) -> None:
        self._fetcher = fetcher
        self._base = status_url_base.rstrip("/")
        self._ttl = ttl_seconds
        self._now = now
        self._cache: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def status_url(self, passport_id: str) -> str:
        return f"{self._base}/passport/{passport_id}/status"

    def _fetch(self, passport_id: str) -> str:
        return check_status(self.status_url(passport_id), self._fetcher)

    def status(self, passport_id: str) -> str:
        """Cached lookup — for read/write actions. Staleness is bounded by the
        TTL and paid for by not making a network call per tool invocation."""
        now = _clock(self._now)
        with self._lock:
            hit = self._cache.get(passport_id)
            if hit is not None and (now - hit[1]).total_seconds() < self._ttl:
                return hit[0]
        status = self._fetch(passport_id)
        with self._lock:
            self._cache[passport_id] = (status, now)
        return status

    def status_live(self, passport_id: str) -> str:
        """Live lookup — for irreversible actions. Bypasses the cache on read
        AND does not populate it on write: writing here would let one
        irreversible check warm an entry a later *normal* call then trusts for
        the rest of the TTL. That is a bypass, not a style point."""
        return self._fetch(passport_id)


# --------------------------------------------------------------------------- #
# The pipeline.
# --------------------------------------------------------------------------- #


def verify_tool_receipt(
    receipt: dict | ToolAccessReceipt,
    jwks: dict,
    *,
    tool: str,
    action_class: ActionClass,
    params_schema: Any,
    nonce_store: NonceStore,
    revocations: RevocationCache,
    bound_values: dict[str, Any] | None = None,
    now: Callable[[], datetime] | datetime | None = None,
    skew_seconds: float = DEFAULT_SKEW_SECONDS,
) -> ToolAccessReceipt:
    """Verify a Tool Access Receipt against *this tool's own* declared shape.

    ``tool``/``action_class``/``params_schema`` are the tool's knowledge, never
    the token's — the token only gets to agree. ``now`` and the fetcher behind
    ``revocations`` are injectable because clock and revocation behaviour are
    exactly what has to be driven in a test.

    Raises a distinct :class:`~agenttic.verifier.sdk.VerifyError` subclass on
    every rejection path, including the unexpected ones.
    """
    raw = (receipt.model_dump(mode="json")
           if isinstance(receipt, ToolAccessReceipt) else receipt)
    try:
        # 0 — typ supported. Reject unknown types so no other signed Agenttic
        #     artifact can be presented here (cross-protocol confusion).
        if not isinstance(raw, dict) or raw.get("typ") != TYP:
            raise VerifyError("unsupported receipt typ")

        # 1 — signature vs JWKS (kid → key), before trusting any other field.
        verify_receipt(raw, jwks)  # TamperedError / UnknownKeyError
        r = ToolAccessReceipt.model_validate(raw)

        # 2 — now ∈ [not_before - skew, expires_at)
        t = _clock(now)
        not_before = _utc(r.not_before or r.issued_at)
        if (t - not_before).total_seconds() < -skew_seconds:
            raise ExpiredError("receipt is not yet valid")
        if t >= _utc(r.expires_at):
            raise ExpiredError("receipt expired")

        # 3 — action shape. action_class is authenticated inside the hash, so an
        #     attacker cannot downgrade irreversible→write to skip step 4.
        if r.action_hash != compute_action_hash(tool, action_class, params_schema):
            raise ActionMismatchError("receipt does not authorise this action")

        # 4 — instance binding, only where instance-correctness is load-bearing.
        if action_class == "irreversible":
            if not bound_values:
                raise ActionMismatchError("irreversible action requires bound params")
            if sorted(bound_values) != sorted(r.bound_param_names or []):
                raise ActionMismatchError("bound param names do not match")
            if compute_bound_params(r.nonce, bound_values) != r.bound_params:
                raise ActionMismatchError("receipt is bound to another instance")

        # 5 — passport revocation (per-passport; revocation beats a valid sig).
        status = (revocations.status_live(r.passport_id)
                  if action_class == "irreversible"
                  else revocations.status(r.passport_id))
        if status != "active":
            raise RevokedError("passport is not active")

        # 6 — LAST, and the only state mutation: a receipt that was going to
        #     fail never burns its nonce. Claimed before execution; if execution
        #     then fails the nonce stays spent (fail-closed — a fresh receipt is
        #     the retry path).
        if not nonce_store.claim(r.nonce, _utc(r.expires_at)):
            raise ReplayError("receipt already used")

        return r
    except VerifyError:
        raise
    except Exception as exc:  # a surprise is a rejection, never a pass-through
        raise VerifyError(f"receipt rejected ({type(exc).__name__})") from exc


# --------------------------------------------------------------------------- #
# The decorator.
# --------------------------------------------------------------------------- #

# Reasons are categories, not internals: enough for an operator to act on,
# nothing an attacker can use to probe which check failed on what value.
_REASONS: dict[type, str] = {
    TamperedError: "tool access receipt signature invalid",
    UnknownKeyError: "tool access receipt signed by an unknown key",
    ExpiredError: "tool access receipt expired",
    ActionMismatchError: "tool access receipt does not authorise this call",
    RevokedError: "agent passport revoked",
    ReplayError: "tool access receipt already used",
}

DEFAULT_NONCE_STORE = FileNonceStore()
DEFAULT_REVOCATIONS = RevocationCache()


def require_receipt(
    action: str,
    action_class: ActionClass,
    params_schema: Any,
    bound_params: list[str] | None = None,
    *,
    jwks: dict | Callable[[], dict] | None = None,
    nonce_store: NonceStore | None = None,
    revocations: RevocationCache | None = None,
    now: Callable[[], datetime] | datetime | None = None,
    skew_seconds: float = DEFAULT_SKEW_SECONDS,
):
    """Refuse any call to this endpoint that doesn't carry a valid, current,
    action-matched, single-use Tool Access Receipt (403).

    ``bound_params`` names the endpoint arguments an *irreversible* action is
    bound to; their actual values at call time are re-hashed and compared, so a
    receipt minted for ``delete_customer(123)`` cannot execute ``delete(456)``.

    ``jwks`` may be a dict or a callable returning one (fetched once, cached by
    the caller). No JWKS configured ⇒ everything is rejected — fail closed.
    """

    def decorator(func):
        sig = inspect.signature(func)
        req_name = next((n for n, p in sig.parameters.items()
                         if p.annotation in (Request, "Request")), None)
        params = list(sig.parameters.values())
        if req_name is None:
            # The endpoint didn't ask for the Request; add one so FastAPI
            # injects it, without touching the endpoint's own signature.
            params.append(inspect.Parameter(_INJECTED_REQUEST,
                                            inspect.Parameter.KEYWORD_ONLY,
                                            annotation=Request))

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.pop(_INJECTED_REQUEST, None)
            if request is None and req_name is not None:
                bound = sig.bind_partial(*args, **kwargs)
                request = bound.arguments.get(req_name)
            header = request.headers.get(HEADER_NAME) if request is not None else None
            if not header:
                raise HTTPException(403, "missing tool access receipt")

            values = None
            if bound_params:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                values = {n: bound.arguments.get(n) for n in bound_params}

            keys = jwks() if callable(jwks) else jwks
            try:
                verify_tool_receipt(
                    decode_receipt_header(header), keys or {"keys": []},
                    tool=action, action_class=action_class,
                    params_schema=params_schema,
                    nonce_store=nonce_store or DEFAULT_NONCE_STORE,
                    revocations=revocations or DEFAULT_REVOCATIONS,
                    bound_values=values, now=now, skew_seconds=skew_seconds)
            except VerifyError as exc:
                raise HTTPException(
                    403, _REASONS.get(type(exc), "tool access receipt rejected"))
            except Exception:  # unparseable header, bad base64, anything
                raise HTTPException(403, "tool access receipt rejected")

            result = func(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        # functools.wraps sets __wrapped__, which inspect.signature would follow
        # back to the un-injected signature — __signature__ wins over it.
        wrapper.__signature__ = sig.replace(parameters=params)
        return wrapper

    return decorator


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clock(now: Callable[[], datetime] | datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return _utc(now() if callable(now) else now)
