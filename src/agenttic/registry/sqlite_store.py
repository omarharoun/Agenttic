"""Registry — versioned storage (Step 6), SQLite by default, Postgres-capable.

Principles:
* Append-only versioning: a (suite_id, version) or (rubric_id, version) pair
  can never be overwritten. Updating means saving the next version.
* Scorecards record the exact suite+rubric versions used, so any historical
  run is reproducible.
* Live-path data (production traces, live scores, re-eval requests) lives in
  separate tables and never mixes into batch scorecards (Step 9 criterion).

The only permitted in-place updates are the suite approval flag and the catalog
``active`` flag — gate/catalog state, not content.

**Tenancy.** Every table carries a ``tenant_id``. A Registry is bound to one
tenant and scopes every read/write by it. With SQLite the default deployment is
DB-per-tenant (the file is the boundary; ``tenant_id`` stays "default"); with
Postgres a single database is shared and ``tenant_id`` provides row-level
isolation (see ``server.app.Workspaces``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import UniqueConstraint, event, func
from sqlmodel import Field, Session, SQLModel, create_engine, select

from agenttic.schema.agent import DeclaredAgent
from agenttic.schema.scorecard import Scorecard
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.rubric import Rubric
from agenttic.schema.trace import Trace

DEFAULT_TENANT = "default"


class DuplicateVersionError(RuntimeError):
    """Attempted to overwrite an existing (id, version) pair."""


class NotFoundError(KeyError):
    pass


class SuiteRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "suite_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    suite_id: str = Field(index=True)
    version: int
    approved: bool = False
    payload: str


class CaseRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "suite_id", "suite_version",
                                       "test_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    suite_id: str = Field(index=True)
    suite_version: int
    test_id: str
    payload: str


class RubricRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "rubric_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    rubric_id: str = Field(index=True)
    version: int
    payload: str


class DeclaredAgentRow(SQLModel, table=True):
    """The pre-registered agent catalog. Versioned + append-only like suites
    and rubrics — editing an agent stores the next version. ``active`` is the
    one permitted in-place flag (a retire toggle, like the suite approval gate);
    it is catalog state, not connection content."""
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    version: int
    active: bool = True
    created_at: datetime
    payload: str


class TraceRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "trace_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    trace_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    mode: str = Field(index=True)  # "batch" | "live"
    created_at: datetime
    payload: str


class ScorecardRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "scorecard_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    scorecard_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    suite_version: int
    created_at: datetime
    payload: str


class ABComparisonRow(SQLModel, table=True):
    """One A/B comparison run. ``status`` tracks the background run lifecycle
    (running -> succeeded/failed); ``payload`` holds the serialized
    :class:`agenttic.schema.ab.ABComparison` once the run completes."""
    __table_args__ = (UniqueConstraint("tenant_id", "comparison_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    comparison_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    status: str = Field(default="running")   # running | succeeded | failed
    error: str = ""
    created_at: datetime
    payload: str = ""                        # ABComparison JSON when done


class OptimizationRunRow(SQLModel, table=True):
    """One prompt-optimization run. ``status`` tracks the background lifecycle
    (running -> succeeded/failed); ``payload`` holds the serialized
    :class:`agenttic.schema.optimization.OptimizationRun` (baseline→best prompt
    lineage + train/heldout scores) once it completes. Append-only."""
    __table_args__ = (UniqueConstraint("tenant_id", "run_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    run_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    status: str = Field(default="running")   # running | succeeded | failed
    error: str = ""
    created_at: datetime
    payload: str = ""                        # OptimizationRun JSON when done


class CanonicalRunRow(SQLModel, table=True):
    """One standard-benchmark run for an agent: the full canonical metric bundle
    (tool-call accuracy, refusal, injection, pass^k, calibration) + the Agenttic
    Index, computed from k repeated runs of the standard/dataset suites."""
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    run_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    created_at: datetime
    payload: str = ""                        # canonical result JSON


class LiveScoreRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    trace_id: str
    criterion_id: str = Field(index=True)
    score: float
    created_at: datetime


class ReEvalRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    reason: str
    created_at: datetime


class SpendRow(SQLModel, table=True):
    """Append-only ledger of LLM spend, for the daily budget cap."""
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    day: str = Field(index=True)  # UTC YYYY-MM-DD
    model: str
    cost_usd: float
    created_at: datetime


class UserRow(SQLModel, table=True):
    """A login account. GLOBAL table (lookup by email, not tenant-scoped) — the
    user authenticates first, then their ``tenant_id``/``role`` drive the
    existing tenant scoping + RBAC. Passwords are bcrypt hashes, never plaintext."""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    password_hash: str
    role: str = "viewer"                       # viewer | operator | admin
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    created_at: datetime
    verified: bool = Field(default=False)      # email confirmed via a token


class ApiKeyRow(SQLModel, table=True):
    """A tenant's own provider API key, ENCRYPTED at rest. GLOBAL table keyed by
    (tenant_id, provider). The ciphertext is never returned by the API; only a
    masked ``…last4`` is surfaced. Every Anthropic call for a tenant's run uses
    this key — the platform key is never used for tenant runs."""
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    provider: str = "anthropic"
    ciphertext: str
    last4: str
    created_at: datetime
    updated_at: datetime


class AgentConnectionRow(SQLModel, table=True):
    """A tenant's saved "Connect your agent" config — the live HTTP endpoint the
    Safety Battery is scanned against. One per tenant (upsert, like api_keys).

    The auth header VALUE is a secret: stored ENCRYPTED (Fernet) in
    ``auth_ciphertext``, never returned by the API (only ``auth_last4`` masked).
    The request/response mapping (which field the prompt goes into; the path to
    the reply) is plain config. ``consent`` + ``consent_at`` record that the user
    confirmed they own/are-authorized-to-test the agent — scanning is blocked
    without it."""
    __tablename__ = "agent_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    name: str = "default"                       # reserved for future multi-agent
    agent_name: str = "your-agent"              # display name for the certificate
    endpoint_url: str = ""
    preset: str = "generic"                     # openai | generic | custom
    request_field: str = "input"                # generic/custom prompt field
    response_path: str = "output"               # dotted path to the reply text
    model: str = ""                             # openai preset model
    auth_header_name: str = ""                  # e.g. "Authorization" (not secret)
    auth_ciphertext: str = ""                   # Fernet(auth header value)
    auth_last4: str = ""                         # masked display only
    consent: bool = False
    consent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AssistantSessionRow(SQLModel, table=True):
    """One Safe Reference Assistant conversation, tenant-scoped. ``payload`` is
    the full JSON-serializable session state (transcript, scratchpad, step log,
    any pending sensitive action). ``status`` is denormalized for listing /
    "is this session waiting on me?" without parsing the payload. Append-or-
    update by (tenant_id, session_id)."""
    __tablename__ = "assistant_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    session_id: str = Field(index=True)
    status: str = "ready"                       # ready | awaiting_approval
    payload: str = ""                           # full session-state JSON
    created_at: datetime
    updated_at: datetime


class CopilotSessionRow(SQLModel, table=True):
    """One in-app Copilot AGENT conversation, tenant-scoped. Like
    ``AssistantSessionRow`` but for the platform Copilot: ``payload`` is the full
    JSON session state (Anthropic transcript with tool_use/tool_result blocks,
    step log, any pending write-action awaiting the user's confirmation).
    ``status`` (ready | awaiting_approval) is denormalized for listing. Upsert by
    (tenant_id, session_id)."""
    __tablename__ = "copilot_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    session_id: str = Field(index=True)
    status: str = "ready"                       # ready | awaiting_approval
    payload: str = ""                           # full session-state JSON
    created_at: datetime
    updated_at: datetime


class PersonalApiTokenRow(SQLModel, table=True):
    """A user's personal API token (PAT) for programmatic REST access. GLOBAL
    table (like users): the token is presented as ``Authorization: Bearer`` and
    authenticates the request AS its owning user — same tenant + role as their
    login. Only the SHA-256 hash is stored (never plaintext, never logged); the
    plaintext is shown to the user exactly once at creation. ``revoked_at`` set
    => immediately rejected."""
    __tablename__ = "personal_api_tokens"
    __table_args__ = (UniqueConstraint("token_hash"),)
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True)        # sha256 hex of the full token
    name: str = ""                              # user-chosen label
    user_email: str = Field(index=True)         # owning account
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    role: str = "viewer"                        # snapshot of owner's role
    last4: str = ""                             # for masked display
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ResultCacheRow(SQLModel, table=True):
    """Maps a deterministic run fingerprint -> a completed result, per tenant, so
    an identical run returns the cached scorecard/canonical-run instead of
    re-executing (zero agent/judge spend). Tenant-scoped, append-only-consistent
    (an existing key is updated to the newest result on a forced refresh)."""
    __tablename__ = "result_cache"
    __table_args__ = (UniqueConstraint("tenant_id", "cache_key"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    cache_key: str = Field(index=True)     # sha256 of the run's inputs
    kind: str = "scorecard"                # scorecard | canonical
    ref_id: str                            # scorecard_id or canonical run_id
    created_at: datetime


class CertificationRow(SQLModel, table=True):
    """An issued Agent Safety Certificate. GLOBAL table (like users / PATs):
    issuance is tenant-scoped (``tenant_id``), but a certificate is publicly
    verifiable by ``cert_id`` alone — the public endpoints look it up regardless
    of tenant. The signed canonical payload (grade, scores, config_hash, dates)
    lives in ``payload`` with its HMAC ``signature``; ``revoked_at`` is the only
    mutable field and is deliberately OUTSIDE the signed payload, so revoking a
    cert never invalidates its signature. The denormalised columns are for
    querying/listing only — the payload is the source of truth."""
    __tablename__ = "certifications"
    __table_args__ = (UniqueConstraint("cert_id"),)
    id: int | None = Field(default=None, primary_key=True)
    cert_id: str = Field(index=True)              # public certificate id
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    config_hash: str = Field(index=True)          # ties the cert to that agent version
    scorecard_id: str = Field(index=True)         # the safety run it was issued from
    grade: str                                    # A | B | C | D | F (cached from payload)
    payload: str                                  # canonical signed JSON
    signature: str                                # HMAC-SHA256 hex over the payload
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime


class EmailTokenRow(SQLModel, table=True):
    """A single-use, expiring email token (account verification). GLOBAL, like
    users. Consumed by setting ``used_at``; rows are safe to prune past expiry."""
    __tablename__ = "email_tokens"
    __table_args__ = (UniqueConstraint("token"),)
    id: int | None = Field(default=None, primary_key=True)
    token: str = Field(index=True)
    email: str = Field(index=True)
    purpose: str = "verify"
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Certification track (SPEC-2 M4/M6). Profiles + dossiers + incidents, with
# append-only *_events tables. State that changes over time (a dossier's
# revocation, an incident's lifecycle) is modeled as events; current state is
# computed by folding the event stream. These tables are never UPDATEd.
# --------------------------------------------------------------------------- #


class CertProfileRow(SQLModel, table=True):
    """A pinned certification profile version. Append-only + versioned like
    suites/rubrics."""
    __tablename__ = "cert_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "profile_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    profile_id: str = Field(index=True)
    version: int
    created_at: datetime
    payload: str


class AssertionSetRow(SQLModel, table=True):
    """A pinned assertion-set version (SPEC-13 Step 62). Append-only + versioned
    like suites/rubrics/profiles: which properties were in force is evidence."""
    __tablename__ = "assertion_sets"
    __table_args__ = (UniqueConstraint("tenant_id", "set_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    set_id: str = Field(index=True)
    version: int
    created_at: datetime
    payload: str


class DossierRow(SQLModel, table=True):
    """A certification dossier — immutable once written. Revocation/renewal are
    recorded as dossier_events, never as an UPDATE to this row. Keyed by
    dossier_id; ``content_sha256`` and ``prev_dossier_sha256`` carry the chain."""
    __tablename__ = "dossiers"
    __table_args__ = (UniqueConstraint("tenant_id", "dossier_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    dossier_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    profile_id: str = Field(index=True)
    tier: str = ""
    content_sha256: str = Field(default="", index=True)
    prev_dossier_sha256: str | None = None
    created_at: datetime
    payload: str


class DossierEventRow(SQLModel, table=True):
    """Append-only dossier lifecycle events (created | revoked | renewed).
    Current status is computed by folding these (staleness engine, M7)."""
    __tablename__ = "dossier_events"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    dossier_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    event_type: str = Field(index=True)  # created | revoked | renewed
    reason: str = ""
    created_at: datetime
    payload: str = "{}"


class IncidentRow(SQLModel, table=True):
    """The opening record of an incident. Lifecycle state is *computed* from the
    append-only incident_events stream (FSM in live/incidents.py); this row is
    never UPDATEd."""
    __tablename__ = "incidents"
    __table_args__ = (UniqueConstraint("tenant_id", "incident_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    incident_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    severity: str = Field(index=True)
    origin: str = "manual"
    opened_at: datetime
    payload: str


class IncidentEventRow(SQLModel, table=True):
    """Append-only incident lifecycle events (opened | triaged | reported |
    closed | note). The current state is the fold of these events."""
    __tablename__ = "incident_events"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    incident_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    event_type: str = Field(index=True)  # opened | triaged | reported | closed | note
    actor: str = ""
    note: str = ""
    created_at: datetime
    payload: str = "{}"


class PassportRow(SQLModel, table=True):
    """An issued passport (immutable). Revocation is an append-only
    passport_event, never an UPDATE. Status is computed from the events."""
    __tablename__ = "passports"
    __table_args__ = (UniqueConstraint("tenant_id", "passport_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    passport_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    created_at: datetime
    payload: str


class PassportEventRow(SQLModel, table=True):
    """Append-only passport lifecycle events (issued | revoked)."""
    __tablename__ = "passport_events"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    passport_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    event_type: str = Field(index=True)
    reason: str = ""
    created_at: datetime


class CanarySetRow(SQLModel, table=True):
    """A per-agent versioned canary set (SPEC-2 M15). Append-only + versioned;
    rotation stores the next version. Trip history lives in enforcement_events."""
    __tablename__ = "canary_sets"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    version: int
    created_at: datetime
    payload: str


class CohortRow(SQLModel, table=True):
    """A caller cohort at a release stage. ``stage`` resolves to the current
    stage; changes are recorded as append-only PromotionRecords."""
    __tablename__ = "release_cohorts"
    __table_args__ = (UniqueConstraint("tenant_id", "cohort_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    cohort_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    stage: str = Field(index=True)
    payload: str


class PromotionRecordRow(SQLModel, table=True):
    """Append-only stage-change audit (promotion | demotion). Never UPDATEd."""
    __tablename__ = "release_promotions"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    record_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    cohort_id: str = Field(index=True)
    kind: str = Field(index=True)
    created_at: datetime
    payload: str


class EnforcementPolicyRow(SQLModel, table=True):
    """A compiled enforcement policy, keyed by content hash (append-only —
    recompilation writes a new row; the newest per agent is active)."""
    __tablename__ = "enforcement_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "policy_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    policy_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    content_hash: str = Field(index=True)
    created_at: datetime
    payload: str


class EnforcementEventRow(SQLModel, table=True):
    """The single append-only enforcement log — agent decisions AND admin/judge
    actions. Never UPDATEd (Hard Rule 19)."""
    __tablename__ = "enforcement_events"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    kind: str = Field(index=True)
    action: str = ""
    created_at: datetime
    payload: str


class ApprovalRequestRow(SQLModel, table=True):
    """A parked tool call awaiting human approval. ``state`` resolves
    (pending → approved/denied/expired) like a run lifecycle."""
    __tablename__ = "enforcement_approvals"
    __table_args__ = (UniqueConstraint("tenant_id", "approval_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    approval_id: str = Field(index=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    state: str = Field(default="pending", index=True)
    created_at: datetime
    payload: str


class AgentCardRow(SQLModel, table=True):
    """Append-only, versioned agent cards (SPEC-2 M9). Editing a card stores the
    next version; ``source`` is agenttic | index_import."""
    __tablename__ = "agent_cards"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    version: int
    source: str = "agenttic"
    created_at: datetime
    payload: str


class ElicitationSummaryRow(SQLModel, table=True):
    """Append-only elicitation-matrix summaries per agent (SPEC-2 T13.5). Each
    row is one certification-time neutral-vs-strong analysis."""
    __tablename__ = "elicitation_summaries"
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    inconsistent: bool = False
    underpowered: bool = False
    created_at: datetime
    payload: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _harden_sqlite(engine) -> None:
    """Per-connection PRAGMAs for safe concurrent access: WAL (concurrent
    readers + one writer), a busy timeout (wait instead of 'database is
    locked'), and foreign-key enforcement. WAL persists on the file; the rest
    are connection-scoped, so set them on every connect."""
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def make_engine(url: str):
    """Build a SQLAlchemy engine for ``url`` (sqlite:/// or postgresql+psycopg://),
    applying SQLite hardening when applicable."""
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:
        _harden_sqlite(engine)
    return engine


def default_db_filename() -> str:
    """The default SQLite filename when no ``db_path``/``url`` is configured.

    Rename back-compat (no data loss): new installs use ``agenttic.db``, but if
    a legacy ``ascore.db`` already exists in the working directory — and no
    ``agenttic.db`` does — we keep using it so an existing registry is never
    orphaned. ``agenttic.db`` wins whenever it is present."""
    if Path("agenttic.db").exists():
        return "agenttic.db"
    if Path("ascore.db").exists():
        return "ascore.db"
    return "agenttic.db"


class Registry:
    """Versioned store bound to one tenant. Also satisfies the harness
    TraceStore protocol. Pass ``db_path`` (SQLite file), ``url`` (any backend),
    or a shared ``engine`` (Postgres multi-tenant)."""

    def __init__(self, db_path: str | Path | None = None, *,
                 url: str | None = None, engine=None, tenant: str = DEFAULT_TENANT):
        self.tenant = tenant
        if engine is not None:
            self.engine = engine
        else:
            if url is None:
                name = db_path if db_path is not None else default_db_filename()
                url = f"sqlite:///{name}"
            self.engine = make_engine(url)
        from agenttic.migrations import run_migrations
        run_migrations(self.engine)  # idempotent; versioned schema

    # -- suites / cases ----------------------------------------------------

    def save_suite(self, suite: TestSuite, cases: list[TestCase]) -> None:
        bad = [c.test_id for c in cases if c.suite_id != suite.suite_id]
        if bad:
            raise ValueError(f"cases not belonging to suite {suite.suite_id}: {bad}")
        with Session(self.engine) as s:
            exists = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite.suite_id,
                SuiteRow.version == suite.version)).first()
            if exists:
                raise DuplicateVersionError(
                    f"suite {suite.suite_id} v{suite.version} already stored; "
                    "save the next version instead"
                )
            s.add(SuiteRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                           version=suite.version, approved=suite.approved,
                           payload=suite.model_dump_json()))
            for c in cases:
                s.add(CaseRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                              suite_version=suite.version, test_id=c.test_id,
                              payload=c.model_dump_json()))
            s.commit()

    def get_suite(self, suite_id: str, version: int | None = None
                  ) -> tuple[TestSuite, list[TestCase]]:
        with Session(self.engine) as s:
            q = select(SuiteRow).where(SuiteRow.tenant_id == self.tenant,
                                       SuiteRow.suite_id == suite_id)
            q = q.where(SuiteRow.version == version) if version is not None \
                else q.order_by(SuiteRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            suite = TestSuite.model_validate_json(row.payload)
            suite.approved = row.approved
            case_rows = s.exec(select(CaseRow).where(
                CaseRow.tenant_id == self.tenant,
                CaseRow.suite_id == suite_id,
                CaseRow.suite_version == suite.version)).all()
            cases = [TestCase.model_validate_json(r.payload) for r in case_rows]
            return suite, sorted(cases, key=lambda c: c.test_id)

    def add_cases(self, suite_id: str, version: int, cases: list[TestCase]) -> int:
        """Persist generated cases incrementally (generator checkpointing).
        Idempotent: skips (suite_id, version, test_id) already present. Returns
        the number newly inserted. Lets a failed generation resume instead of
        re-spending tokens for already-generated tasks."""
        added = 0
        with Session(self.engine) as s:
            existing = set(s.exec(select(CaseRow.test_id).where(
                CaseRow.tenant_id == self.tenant, CaseRow.suite_id == suite_id,
                CaseRow.suite_version == version)).all())
            for c in cases:
                if c.test_id in existing:
                    continue
                s.add(CaseRow(tenant_id=self.tenant, suite_id=suite_id,
                              suite_version=version, test_id=c.test_id,
                              payload=c.model_dump_json()))
                added += 1
            s.commit()
        return added

    def peek_cases(self, suite_id: str, version: int) -> list[TestCase]:
        """Cases already checkpointed for (suite_id, version) — for resume.
        Empty if none. Does NOT require a SuiteRow to exist yet."""
        with Session(self.engine) as s:
            rows = s.exec(select(CaseRow).where(
                CaseRow.tenant_id == self.tenant, CaseRow.suite_id == suite_id,
                CaseRow.suite_version == version)).all()
            return sorted((TestCase.model_validate_json(r.payload) for r in rows),
                          key=lambda c: c.test_id)

    def finalize_suite(self, suite: TestSuite) -> None:
        """Insert the SuiteRow once all cases are checkpointed (generator end).
        Idempotent — a re-run that completes simply confirms the existing row."""
        with Session(self.engine) as s:
            exists = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite.suite_id,
                SuiteRow.version == suite.version)).first()
            if exists:
                return
            s.add(SuiteRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                           version=suite.version, approved=suite.approved,
                           payload=suite.model_dump_json()))
            s.commit()

    def approve_suite(self, suite_id: str, version: int) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite_id,
                SuiteRow.version == version)).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            row.approved = True
            s.add(row)
            s.commit()

    def list_suites(self, prefix: str | None = None) -> list[dict]:
        """Latest version of every suite (optionally filtered by suite_id prefix),
        with a case count. For discovery surfaces (e.g. regression suites)."""
        with Session(self.engine) as s:
            q = select(SuiteRow).where(SuiteRow.tenant_id == self.tenant)
            if prefix:
                q = q.where(SuiteRow.suite_id.startswith(prefix))
            rows = s.exec(q.order_by(SuiteRow.suite_id, SuiteRow.version)).all()
            latest: dict[str, SuiteRow] = {}
            for r in rows:  # ordered asc => last write per suite_id wins (latest)
                latest[r.suite_id] = r
            out = []
            for sid, row in latest.items():
                n = len(s.exec(select(CaseRow.test_id).where(
                    CaseRow.tenant_id == self.tenant, CaseRow.suite_id == sid,
                    CaseRow.suite_version == row.version)).all())
                out.append({"suite_id": sid, "version": row.version,
                            "approved": row.approved, "n_cases": n})
            return sorted(out, key=lambda d: d["suite_id"])

    # -- rubrics -------------------------------------------------------------

    def save_rubric(self, rubric: Rubric) -> None:
        with Session(self.engine) as s:
            if s.exec(select(RubricRow).where(
                    RubricRow.tenant_id == self.tenant,
                    RubricRow.rubric_id == rubric.rubric_id,
                    RubricRow.version == rubric.version)).first():
                raise DuplicateVersionError(
                    f"rubric {rubric.rubric_id} v{rubric.version} already stored"
                )
            s.add(RubricRow(tenant_id=self.tenant, rubric_id=rubric.rubric_id,
                            version=rubric.version, payload=rubric.model_dump_json()))
            s.commit()

    def get_rubric(self, rubric_id: str, version: int | None = None) -> Rubric:
        with Session(self.engine) as s:
            q = select(RubricRow).where(RubricRow.tenant_id == self.tenant,
                                        RubricRow.rubric_id == rubric_id)
            q = q.where(RubricRow.version == version) if version is not None \
                else q.order_by(RubricRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"rubric {rubric_id} v{version}")
            return Rubric.model_validate_json(row.payload)

    # -- declared agent catalog ------------------------------------------------

    def register_agent(self, agent: DeclaredAgent) -> DeclaredAgent:
        """Create or update a catalog entry. Create-or-bump semantics (like
        Managed Agent deploy): a new agent_id starts at v1; re-registering an
        existing one stores the next version and reactivates it. Prior versions
        stay on record (append-only)."""
        with Session(self.engine) as s:
            versions = s.exec(select(DeclaredAgentRow.version).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent.agent_id)).all()
            agent = agent.model_copy(
                update={"version": (max(versions) + 1) if versions else 1})
            s.add(DeclaredAgentRow(
                tenant_id=self.tenant, agent_id=agent.agent_id,
                version=agent.version, active=True,
                created_at=_now(), payload=agent.model_dump_json()))
            s.commit()
        return agent

    def get_declared_agent(self, agent_id: str, version: int | None = None
                           ) -> DeclaredAgent:
        with Session(self.engine) as s:
            q = select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent_id)
            q = q.where(DeclaredAgentRow.version == version) if version is not None \
                else q.order_by(DeclaredAgentRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"declared agent {agent_id}")
            return DeclaredAgent.model_validate_json(row.payload)

    def list_declared_agents(self, include_retired: bool = False
                             ) -> list[dict]:
        """Latest version of every declared agent. Each dict is the agent's
        fields plus catalog metadata (``active``, ``created_at``)."""
        with Session(self.engine) as s:
            rows = s.exec(select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant)).all()
        latest: dict[str, DeclaredAgentRow] = {}
        for r in rows:
            if r.agent_id not in latest or r.version > latest[r.agent_id].version:
                latest[r.agent_id] = r
        out = []
        for r in sorted(latest.values(), key=lambda r: r.agent_id):
            if not r.active and not include_retired:
                continue
            agent = DeclaredAgent.model_validate_json(r.payload)
            out.append({**agent.model_dump(), "active": r.active,
                        "created_at": r.created_at.isoformat()})
        return out

    def retire_agent(self, agent_id: str) -> None:
        """Soft-delete: flip every version of this agent to inactive. The
        history stays (append-only); re-registering reactivates it."""
        with Session(self.engine) as s:
            rows = s.exec(select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent_id)).all()
            if not rows:
                raise NotFoundError(f"declared agent {agent_id}")
            for r in rows:
                r.active = False
                s.add(r)
            s.commit()

    # -- traces ----------------------------------------------------------------

    def save_trace(self, trace: Trace, mode: str = "batch") -> None:
        with Session(self.engine) as s:
            s.add(TraceRow(tenant_id=self.tenant, trace_id=trace.trace_id,
                           agent_id=trace.agent_id, mode=mode, created_at=_now(),
                           payload=trace.model_dump_json()))
            s.commit()

    def get_trace(self, trace_id: str) -> Trace:
        with Session(self.engine) as s:
            row = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.trace_id == trace_id)).first()
            if not row:
                raise NotFoundError(f"trace {trace_id}")
            return Trace.model_validate_json(row.payload)

    def traces(self, agent_id: str, mode: str = "batch") -> list[Trace]:
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.agent_id == agent_id, TraceRow.mode == mode)
                .order_by(TraceRow.id)).all()
            return [Trace.model_validate_json(r.payload) for r in rows]

    # -- scorecards --------------------------------------------------------------

    def save_scorecard(self, sc: Scorecard) -> None:
        with Session(self.engine) as s:
            s.add(ScorecardRow(tenant_id=self.tenant, scorecard_id=sc.scorecard_id,
                               agent_id=sc.agent_id, suite_id=sc.suite_id,
                               suite_version=sc.suite_version,
                               created_at=sc.created_at, payload=sc.model_dump_json()))
            s.commit()

    def get_scorecard(self, scorecard_id: str) -> Scorecard:
        with Session(self.engine) as s:
            row = s.exec(select(ScorecardRow).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.scorecard_id == scorecard_id)).first()
            if not row:
                raise NotFoundError(f"scorecard {scorecard_id}")
            return Scorecard.model_validate_json(row.payload)

    def scorecards_for(self, agent_id: str, suite_id: str | None = None
                       ) -> list[Scorecard]:
        with Session(self.engine) as s:
            q = select(ScorecardRow).where(ScorecardRow.tenant_id == self.tenant,
                                           ScorecardRow.agent_id == agent_id)
            if suite_id:
                q = q.where(ScorecardRow.suite_id == suite_id)
            rows = s.exec(q.order_by(ScorecardRow.created_at)).all()
            return [Scorecard.model_validate_json(r.payload) for r in rows]

    def scorecards_in(self, suite_ids) -> list["Scorecard"]:
        """All scorecards (any agent) for the given suites, oldest-first."""
        ids = list(suite_ids)
        if not ids:
            return []
        with Session(self.engine) as s:
            rows = s.exec(select(ScorecardRow).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.suite_id.in_(ids)).order_by(ScorecardRow.created_at)).all()
            return [Scorecard.model_validate_json(r.payload) for r in rows]

    # -- result cache (per-tenant; identical inputs reuse a result, $0 spend) --

    def get_cached_result(self, cache_key: str) -> dict | None:
        """The cached result for a run fingerprint, or None. Tenant-scoped, so a
        tenant never sees another tenant's cached results."""
        with Session(self.engine) as s:
            row = s.exec(select(ResultCacheRow).where(
                ResultCacheRow.tenant_id == self.tenant,
                ResultCacheRow.cache_key == cache_key)).first()
            if row is None:
                return None
            return {"kind": row.kind, "ref_id": row.ref_id,
                    "created_at": row.created_at}

    def put_cached_result(self, cache_key: str, kind: str, ref_id: str) -> None:
        """Record (or refresh) the result a run fingerprint maps to."""
        with Session(self.engine) as s:
            row = s.exec(select(ResultCacheRow).where(
                ResultCacheRow.tenant_id == self.tenant,
                ResultCacheRow.cache_key == cache_key)).first()
            if row is None:
                s.add(ResultCacheRow(tenant_id=self.tenant, cache_key=cache_key,
                                     kind=kind, ref_id=ref_id, created_at=_now()))
            else:
                row.kind, row.ref_id, row.created_at = kind, ref_id, _now()
                s.add(row)
            s.commit()

    def cached_scorecard_ids(self) -> set[str]:
        """scorecard_ids that are the target of a cache entry (i.e. reusable for
        free on an identical re-run). Tenant-scoped."""
        with Session(self.engine) as s:
            rows = s.exec(select(ResultCacheRow.ref_id).where(
                ResultCacheRow.tenant_id == self.tenant,
                ResultCacheRow.kind == "scorecard")).all()
        return set(rows)

    def get_canonical_run(self, run_id: str) -> dict | None:
        import json as _json
        with Session(self.engine) as s:
            row = s.exec(select(CanonicalRunRow).where(
                CanonicalRunRow.tenant_id == self.tenant,
                CanonicalRunRow.run_id == run_id)).first()
        return _json.loads(row.payload) if row else None

    def save_canonical_run(self, run_id: str, agent_id: str, payload: str) -> None:
        with Session(self.engine) as s:
            s.add(CanonicalRunRow(tenant_id=self.tenant, run_id=run_id,
                                  agent_id=agent_id, created_at=_now(),
                                  payload=payload))
            s.commit()

    def latest_canonical_runs(self) -> list[dict]:
        """Latest canonical run per agent (newest first), as parsed payloads."""
        import json as _json
        with Session(self.engine) as s:
            rows = s.exec(select(CanonicalRunRow).where(
                CanonicalRunRow.tenant_id == self.tenant
            ).order_by(CanonicalRunRow.created_at)).all()
        latest: dict[str, dict] = {}
        for r in rows:  # oldest-first => last write per agent wins
            try:
                latest[r.agent_id] = _json.loads(r.payload)
            except Exception:  # noqa: BLE001
                continue
        return sorted(latest.values(), key=lambda d: d.get("index", 0), reverse=True)

    def suites_scored_for(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ScorecardRow.suite_id).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.agent_id == agent_id).distinct()).all()
            return list(rows)

    # -- A/B comparisons -------------------------------------------------------

    def create_ab_run(self, comparison_id: str, suite_id: str) -> None:
        """Insert a 'running' placeholder so the UI can track an in-flight A/B
        run before its comparison artifact exists."""
        with Session(self.engine) as s:
            s.add(ABComparisonRow(
                tenant_id=self.tenant, comparison_id=comparison_id,
                suite_id=suite_id, status="running", created_at=_now()))
            s.commit()

    def save_ab_comparison(self, comparison) -> None:
        """Persist a finished comparison. Upserts: completes the 'running' row
        the manager created, or inserts a new 'succeeded' row (CLI/direct use)."""
        from agenttic.schema.ab import ABComparison
        assert isinstance(comparison, ABComparison)
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison.comparison_id)).first()
            if row is None:
                row = ABComparisonRow(
                    tenant_id=self.tenant,
                    comparison_id=comparison.comparison_id,
                    suite_id=comparison.suite_id, created_at=comparison.created_at)
            row.status = "succeeded"
            row.error = ""
            row.suite_id = comparison.suite_id
            row.payload = comparison.model_dump_json()
            s.add(row)
            s.commit()

    def fail_ab_run(self, comparison_id: str, error: str) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
            if row is None:
                return
            row.status = "failed"
            row.error = error[:500]
            s.add(row)
            s.commit()

    def get_ab_run(self, comparison_id: str) -> dict:
        """Run status + the comparison artifact (parsed, or None while running)."""
        from agenttic.schema.ab import ABComparison
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
        if row is None:
            raise NotFoundError(f"ab comparison {comparison_id}")
        comp = (ABComparison.model_validate_json(row.payload)
                if row.payload else None)
        return {"comparison_id": row.comparison_id, "suite_id": row.suite_id,
                "status": row.status, "error": row.error,
                "created_at": row.created_at.isoformat(),
                "comparison": comp.model_dump(mode="json") if comp else None}

    def get_ab_comparison(self, comparison_id: str):
        """The finished comparison object (raises if it hasn't completed)."""
        from agenttic.schema.ab import ABComparison
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
        if row is None or not row.payload:
            raise NotFoundError(f"ab comparison {comparison_id}")
        return ABComparison.model_validate_json(row.payload)

    def list_ab_runs(self, suite_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant)
            if suite_id:
                q = q.where(ABComparisonRow.suite_id == suite_id)
            rows = s.exec(q.order_by(ABComparisonRow.created_at.desc())).all()
        from agenttic.schema.ab import ABComparison
        out = []
        for r in rows:
            summary = {"comparison_id": r.comparison_id, "suite_id": r.suite_id,
                       "status": r.status, "error": r.error,
                       "created_at": r.created_at.isoformat(),
                       "label_a": None, "label_b": None, "winner": None,
                       "verdict": None}
            if r.payload:
                c = ABComparison.model_validate_json(r.payload)
                summary.update(label_a=c.label_a, label_b=c.label_b,
                               winner=c.winner, verdict=c.verdict,
                               success_rate_a=c.success_rate_a,
                               success_rate_b=c.success_rate_b,
                               n_paired=c.n_paired)
            out.append(summary)
        return out

    # -- prompt-optimization runs ----------------------------------------------

    def create_optimization_run(self, run_id: str, agent_id: str,
                                suite_id: str) -> None:
        """Insert a 'running' placeholder so the UI can track an in-flight
        optimization before its artifact exists."""
        with Session(self.engine) as s:
            s.add(OptimizationRunRow(
                tenant_id=self.tenant, run_id=run_id, agent_id=agent_id,
                suite_id=suite_id, status="running", created_at=_now()))
            s.commit()

    def save_optimization_run(self, run) -> None:
        """Persist a finished optimization run. Upserts: completes the 'running'
        row the manager created, or inserts a 'succeeded' row (CLI/direct use)."""
        from agenttic.schema.optimization import OptimizationRun
        assert isinstance(run, OptimizationRun)
        with Session(self.engine) as s:
            row = s.exec(select(OptimizationRunRow).where(
                OptimizationRunRow.tenant_id == self.tenant,
                OptimizationRunRow.run_id == run.run_id)).first()
            if row is None:
                row = OptimizationRunRow(
                    tenant_id=self.tenant, run_id=run.run_id,
                    agent_id=run.agent_id, suite_id=run.suite_id,
                    created_at=run.created_at)
            row.status = run.status
            row.error = run.error[:500] if run.error else ""
            row.agent_id = run.agent_id
            row.suite_id = run.suite_id
            row.payload = run.model_dump_json()
            s.add(row)
            s.commit()

    def fail_optimization_run(self, run_id: str, error: str) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(OptimizationRunRow).where(
                OptimizationRunRow.tenant_id == self.tenant,
                OptimizationRunRow.run_id == run_id)).first()
            if row is None:
                return
            row.status = "failed"
            row.error = error[:500]
            s.add(row)
            s.commit()

    def get_optimization_run(self, run_id: str) -> dict:
        """Run status + the artifact (parsed, or None while running)."""
        from agenttic.schema.optimization import OptimizationRun
        with Session(self.engine) as s:
            row = s.exec(select(OptimizationRunRow).where(
                OptimizationRunRow.tenant_id == self.tenant,
                OptimizationRunRow.run_id == run_id)).first()
        if row is None:
            raise NotFoundError(f"optimization run {run_id}")
        run = (OptimizationRun.model_validate_json(row.payload)
               if row.payload else None)
        return {"run_id": row.run_id, "agent_id": row.agent_id,
                "suite_id": row.suite_id, "status": row.status,
                "error": row.error, "created_at": row.created_at.isoformat(),
                "run": run.model_dump(mode="json") if run else None}

    def get_optimization_artifact(self, run_id: str):
        """The finished OptimizationRun object (raises if it hasn't completed)."""
        from agenttic.schema.optimization import OptimizationRun
        with Session(self.engine) as s:
            row = s.exec(select(OptimizationRunRow).where(
                OptimizationRunRow.tenant_id == self.tenant,
                OptimizationRunRow.run_id == run_id)).first()
        if row is None or not row.payload:
            raise NotFoundError(f"optimization run {run_id}")
        return OptimizationRun.model_validate_json(row.payload)

    def list_optimization_runs(self, agent_id: str | None = None,
                               suite_id: str | None = None) -> list[dict]:
        from agenttic.schema.optimization import OptimizationRun
        with Session(self.engine) as s:
            q = select(OptimizationRunRow).where(
                OptimizationRunRow.tenant_id == self.tenant)
            if agent_id:
                q = q.where(OptimizationRunRow.agent_id == agent_id)
            if suite_id:
                q = q.where(OptimizationRunRow.suite_id == suite_id)
            rows = s.exec(q.order_by(OptimizationRunRow.created_at.desc())).all()
        out = []
        for r in rows:
            summary = {"run_id": r.run_id, "agent_id": r.agent_id,
                       "suite_id": r.suite_id, "status": r.status,
                       "error": r.error, "created_at": r.created_at.isoformat()}
            if r.payload:
                run = OptimizationRun.model_validate_json(r.payload)
                summary.update(
                    best_version=run.best_version, improved=run.improved,
                    baseline_train_rate=run.baseline_train_rate,
                    best_train_rate=run.best_train_rate,
                    baseline_heldout_rate=run.baseline_heldout_rate,
                    best_heldout_rate=run.best_heldout_rate,
                    overfit_gap=run.overfit_gap, total_cost_usd=run.total_cost_usd,
                    n_train=run.n_train, n_heldout=run.n_heldout)
            out.append(summary)
        return out

    # -- live path (Step 9) ----------------------------------------------------

    def save_live_scores(self, agent_id: str, trace_id: str,
                         scores: dict[str, float]) -> None:
        with Session(self.engine) as s:
            for cid, val in scores.items():
                s.add(LiveScoreRow(tenant_id=self.tenant, agent_id=agent_id,
                                   trace_id=trace_id, criterion_id=cid, score=val,
                                   created_at=_now()))
            s.commit()

    def live_scores(self, agent_id: str, criterion_id: str, last_n: int
                    ) -> list[float]:
        with Session(self.engine) as s:
            rows = s.exec(select(LiveScoreRow).where(
                LiveScoreRow.tenant_id == self.tenant,
                LiveScoreRow.agent_id == agent_id,
                LiveScoreRow.criterion_id == criterion_id)
                .order_by(LiveScoreRow.id.desc()).limit(last_n)).all()
            return [r.score for r in rows]

    def live_trace_scores(self, agent_id: str | None = None, *,
                          last_n: int = 500) -> list[dict]:
        """Sampled live scores grouped by *trace*, newest-first. Each entry is
        ``{agent_id, trace_id, scores: {criterion_id: score}, created_at}``.

        ``live_scores`` answers the drift question (rolling mean of one
        criterion); this answers the *catch* question the hardening loop needs —
        which individual production traces scored badly and could be promoted
        into a regression suite. ``agent_id=None`` spans every agent."""
        with Session(self.engine) as s:
            q = select(LiveScoreRow).where(LiveScoreRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(LiveScoreRow.agent_id == agent_id)
            rows = s.exec(q.order_by(LiveScoreRow.id.desc())).all()
        by_trace: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []
        for r in rows:                       # rows are newest-first
            key = (r.agent_id, r.trace_id)
            entry = by_trace.get(key)
            if entry is None:
                entry = {"agent_id": r.agent_id, "trace_id": r.trace_id,
                         "scores": {}, "created_at": r.created_at}
                by_trace[key] = entry
                order.append(key)
            # first time we see a (trace, criterion) is its latest row
            entry["scores"].setdefault(r.criterion_id, r.score)
        return [by_trace[k] for k in order[:last_n]]

    def save_reeval_request(self, agent_id: str, reason: str) -> None:
        with Session(self.engine) as s:
            s.add(ReEvalRow(tenant_id=self.tenant, agent_id=agent_id,
                            reason=reason, created_at=_now()))
            s.commit()

    def reeval_requests(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReEvalRow).where(
                ReEvalRow.tenant_id == self.tenant,
                ReEvalRow.agent_id == agent_id).order_by(ReEvalRow.id)).all()
            return [r.reason for r in rows]

    # -- certification profiles (append-only, versioned) -----------------------

    def save_profile(self, profile) -> None:
        """Persist a certification profile version. Append-only: re-saving an
        existing (profile_id, version) raises."""
        with Session(self.engine) as s:
            exists = s.exec(select(CertProfileRow).where(
                CertProfileRow.tenant_id == self.tenant,
                CertProfileRow.profile_id == profile.profile_id,
                CertProfileRow.version == profile.version)).first()
            if exists:
                raise DuplicateVersionError(
                    f"profile {profile.profile_id} v{profile.version} already stored")
            s.add(CertProfileRow(
                tenant_id=self.tenant, profile_id=profile.profile_id,
                version=profile.version, created_at=_now(),
                payload=profile.model_dump_json()))
            s.commit()

    def get_profile(self, profile_id: str, version: int | None = None):
        from agenttic.schema.certification import CertificationProfile
        with Session(self.engine) as s:
            q = select(CertProfileRow).where(
                CertProfileRow.tenant_id == self.tenant,
                CertProfileRow.profile_id == profile_id)
            q = q.where(CertProfileRow.version == version) if version is not None \
                else q.order_by(CertProfileRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"profile {profile_id} v{version}")
            return CertificationProfile.model_validate_json(row.payload)

    def list_profiles(self) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(CertProfileRow).where(
                CertProfileRow.tenant_id == self.tenant
            ).order_by(CertProfileRow.profile_id, CertProfileRow.version)).all()
            return [{"profile_id": r.profile_id, "version": r.version} for r in rows]

    # -- assertion sets (versioned, append-only) ------------------------------

    def save_assertion_set(self, aset) -> None:
        """Persist an assertion-set version. Append-only: re-saving an existing
        (set_id, version) raises, so a set can never be silently edited."""
        with Session(self.engine) as s:
            exists = s.exec(select(AssertionSetRow).where(
                AssertionSetRow.tenant_id == self.tenant,
                AssertionSetRow.set_id == aset.set_id,
                AssertionSetRow.version == aset.version)).first()
            if exists:
                raise DuplicateVersionError(
                    f"assertion set {aset.set_id} v{aset.version} already stored")
            s.add(AssertionSetRow(
                tenant_id=self.tenant, set_id=aset.set_id,
                version=aset.version, created_at=_now(),
                payload=aset.model_dump_json()))
            s.commit()

    def get_assertion_set(self, set_id: str, version: int | None = None):
        from agenttic.schema.assertion_set import AssertionSet
        with Session(self.engine) as s:
            q = select(AssertionSetRow).where(
                AssertionSetRow.tenant_id == self.tenant,
                AssertionSetRow.set_id == set_id)
            q = q.where(AssertionSetRow.version == version) if version is not None \
                else q.order_by(AssertionSetRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"assertion set {set_id} v{version}")
            return AssertionSet.model_validate_json(row.payload)

    def list_assertion_sets(self) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(AssertionSetRow).where(
                AssertionSetRow.tenant_id == self.tenant
            ).order_by(AssertionSetRow.set_id, AssertionSetRow.version)).all()
            return [{"set_id": r.set_id, "version": r.version} for r in rows]

    # -- dossiers (immutable) + append-only dossier_events ---------------------

    def save_dossier(self, dossier) -> None:
        """Persist an immutable dossier and record a 'created' event."""
        with Session(self.engine) as s:
            exists = s.exec(select(DossierRow).where(
                DossierRow.tenant_id == self.tenant,
                DossierRow.dossier_id == dossier.dossier_id)).first()
            if exists:
                raise DuplicateVersionError(
                    f"dossier {dossier.dossier_id} already stored (immutable)")
            s.add(DossierRow(
                tenant_id=self.tenant, dossier_id=dossier.dossier_id,
                agent_id=dossier.agent_id, profile_id=dossier.profile_id,
                tier=dossier.tier_decision.tier,
                content_sha256=dossier.content_sha256 or "",
                prev_dossier_sha256=dossier.prev_dossier_sha256,
                created_at=_now(), payload=dossier.model_dump_json()))
            s.add(DossierEventRow(
                tenant_id=self.tenant, dossier_id=dossier.dossier_id,
                agent_id=dossier.agent_id, event_type="created",
                reason="", created_at=_now(), payload="{}"))
            s.commit()

    def get_dossier(self, dossier_id: str):
        from agenttic.schema.certification import Dossier
        with Session(self.engine) as s:
            row = s.exec(select(DossierRow).where(
                DossierRow.tenant_id == self.tenant,
                DossierRow.dossier_id == dossier_id)).first()
            if not row:
                raise NotFoundError(f"dossier {dossier_id}")
            return Dossier.model_validate_json(row.payload)

    def list_dossiers(self, agent_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(DossierRow).where(DossierRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(DossierRow.agent_id == agent_id)
            rows = s.exec(q.order_by(DossierRow.id)).all()
            return [{"dossier_id": r.dossier_id, "agent_id": r.agent_id,
                     "profile_id": r.profile_id, "tier": r.tier,
                     "content_sha256": r.content_sha256,
                     "prev_dossier_sha256": r.prev_dossier_sha256} for r in rows]

    def latest_dossier(self, agent_id: str):
        with Session(self.engine) as s:
            row = s.exec(select(DossierRow).where(
                DossierRow.tenant_id == self.tenant,
                DossierRow.agent_id == agent_id
            ).order_by(DossierRow.id.desc())).first()
            if not row:
                raise NotFoundError(f"no dossier for agent {agent_id}")
            from agenttic.schema.certification import Dossier
            return Dossier.model_validate_json(row.payload)

    def append_dossier_event(self, dossier_id: str, agent_id: str,
                             event_type: str, reason: str = "",
                             payload: str = "{}") -> None:
        with Session(self.engine) as s:
            s.add(DossierEventRow(
                tenant_id=self.tenant, dossier_id=dossier_id, agent_id=agent_id,
                event_type=event_type, reason=reason,
                created_at=_now(), payload=payload))
            s.commit()

    def list_dossier_events(self, dossier_id: str) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(DossierEventRow).where(
                DossierEventRow.tenant_id == self.tenant,
                DossierEventRow.dossier_id == dossier_id
            ).order_by(DossierEventRow.id)).all()
            return [{"event_type": r.event_type, "reason": r.reason,
                     "created_at": r.created_at.isoformat(),
                     "payload": r.payload} for r in rows]

    # -- incidents (opening record) + append-only incident_events --------------

    def save_incident(self, incident) -> None:
        """Persist an incident's opening record + its 'opened' event."""
        with Session(self.engine) as s:
            exists = s.exec(select(IncidentRow).where(
                IncidentRow.tenant_id == self.tenant,
                IncidentRow.incident_id == incident.incident_id)).first()
            if exists:
                raise DuplicateVersionError(
                    f"incident {incident.incident_id} already opened")
            s.add(IncidentRow(
                tenant_id=self.tenant, incident_id=incident.incident_id,
                agent_id=incident.agent_id, severity=incident.severity,
                origin=incident.origin, opened_at=incident.opened_at,
                payload=incident.model_dump_json()))
            s.add(IncidentEventRow(
                tenant_id=self.tenant, incident_id=incident.incident_id,
                agent_id=incident.agent_id, event_type="opened",
                actor=incident.origin, note=incident.title,
                created_at=incident.opened_at, payload="{}"))
            s.commit()

    def get_incident_record(self, incident_id: str):
        from agenttic.schema.incident import Incident
        with Session(self.engine) as s:
            row = s.exec(select(IncidentRow).where(
                IncidentRow.tenant_id == self.tenant,
                IncidentRow.incident_id == incident_id)).first()
            if not row:
                raise NotFoundError(f"incident {incident_id}")
            return Incident.model_validate_json(row.payload)

    def append_incident_event(self, incident_id: str, agent_id: str,
                              event_type: str, actor: str = "", note: str = "",
                              payload: str = "{}") -> None:
        with Session(self.engine) as s:
            s.add(IncidentEventRow(
                tenant_id=self.tenant, incident_id=incident_id, agent_id=agent_id,
                event_type=event_type, actor=actor, note=note,
                created_at=_now(), payload=payload))
            s.commit()

    def list_incident_events(self, incident_id: str) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(IncidentEventRow).where(
                IncidentEventRow.tenant_id == self.tenant,
                IncidentEventRow.incident_id == incident_id
            ).order_by(IncidentEventRow.id)).all()
            return [{"event_type": r.event_type, "actor": r.actor, "note": r.note,
                     "created_at": r.created_at.isoformat(),
                     "payload": r.payload} for r in rows]

    def list_incidents(self, agent_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(IncidentRow).where(IncidentRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(IncidentRow.agent_id == agent_id)
            rows = s.exec(q.order_by(IncidentRow.id)).all()
            return [{"incident_id": r.incident_id, "agent_id": r.agent_id,
                     "severity": r.severity, "origin": r.origin,
                     "opened_at": r.opened_at.isoformat()} for r in rows]

    # -- passports (immutable) + append-only passport_events -------------------

    def save_passport(self, passport) -> None:
        with Session(self.engine) as s:
            exists = s.exec(select(PassportRow).where(
                PassportRow.tenant_id == self.tenant,
                PassportRow.passport_id == passport.passport_id)).first()
            if exists:
                raise DuplicateVersionError(
                    f"passport {passport.passport_id} already issued")
            s.add(PassportRow(
                tenant_id=self.tenant, passport_id=passport.passport_id,
                agent_id=passport.claims.agent_id, created_at=_now(),
                payload=passport.model_dump_json()))
            s.add(PassportEventRow(
                tenant_id=self.tenant, passport_id=passport.passport_id,
                agent_id=passport.claims.agent_id, event_type="issued",
                created_at=_now()))
            s.commit()

    def get_passport(self, passport_id: str):
        from agenttic.schema.passport import Passport
        with Session(self.engine) as s:
            row = s.exec(select(PassportRow).where(
                PassportRow.tenant_id == self.tenant,
                PassportRow.passport_id == passport_id)).first()
            if not row:
                raise NotFoundError(f"passport {passport_id}")
            return Passport.model_validate_json(row.payload)

    def append_passport_event(self, passport_id: str, agent_id: str,
                              event_type: str, reason: str = "") -> None:
        with Session(self.engine) as s:
            s.add(PassportEventRow(
                tenant_id=self.tenant, passport_id=passport_id, agent_id=agent_id,
                event_type=event_type, reason=reason, created_at=_now()))
            s.commit()

    def passport_status(self, passport_id: str) -> str:
        with Session(self.engine) as s:
            rows = s.exec(select(PassportEventRow).where(
                PassportEventRow.tenant_id == self.tenant,
                PassportEventRow.passport_id == passport_id
            ).order_by(PassportEventRow.id)).all()
            status = "active"
            for r in rows:
                if r.event_type == "revoked":
                    status = "revoked"
            return status

    def list_passports(self, agent_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(PassportRow).where(PassportRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(PassportRow.agent_id == agent_id)
            rows = s.exec(q.order_by(PassportRow.id)).all()
            return [{"passport_id": r.passport_id, "agent_id": r.agent_id,
                     "status": self.passport_status(r.passport_id)} for r in rows]

    # -- canary sets (append-only, versioned) ----------------------------------

    def save_canary_set(self, canary) -> None:
        from agenttic.schema.enforcement import CanarySet
        with Session(self.engine) as s:
            latest = s.exec(select(CanarySetRow).where(
                CanarySetRow.tenant_id == self.tenant,
                CanarySetRow.agent_id == canary.agent_id
            ).order_by(CanarySetRow.version.desc())).first()
            version = canary.version
            if latest is not None and version <= latest.version:
                version = latest.version + 1
                canary = canary.model_copy(update={"version": version})
            s.add(CanarySetRow(
                tenant_id=self.tenant, agent_id=canary.agent_id, version=version,
                created_at=_now(), payload=canary.model_dump_json()))
            s.commit()

    def active_canary_set(self, agent_id: str):
        from agenttic.schema.enforcement import CanarySet
        with Session(self.engine) as s:
            row = s.exec(select(CanarySetRow).where(
                CanarySetRow.tenant_id == self.tenant,
                CanarySetRow.agent_id == agent_id
            ).order_by(CanarySetRow.version.desc())).first()
            return CanarySet.model_validate_json(row.payload) if row else None

    # -- release cohorts + promotion records -----------------------------------

    def save_cohort(self, cohort) -> None:
        with Session(self.engine) as s:
            exists = s.exec(select(CohortRow).where(
                CohortRow.tenant_id == self.tenant,
                CohortRow.cohort_id == cohort.cohort_id)).first()
            if exists:
                raise DuplicateVersionError(f"cohort {cohort.cohort_id} exists")
            s.add(CohortRow(tenant_id=self.tenant, cohort_id=cohort.cohort_id,
                            agent_id=cohort.agent_id, stage=cohort.stage,
                            payload=cohort.model_dump_json()))
            s.commit()

    def get_cohort(self, cohort_id: str):
        from agenttic.schema.release import Cohort
        with Session(self.engine) as s:
            row = s.exec(select(CohortRow).where(
                CohortRow.tenant_id == self.tenant,
                CohortRow.cohort_id == cohort_id)).first()
            if not row:
                raise NotFoundError(f"cohort {cohort_id}")
            return Cohort.model_validate_json(row.payload)

    def set_cohort_stage(self, cohort_id: str, stage: str) -> None:
        """The one permitted in-place field (like the suite approval flag) — the
        current stage. The transition itself is recorded as a PromotionRecord."""
        from agenttic.schema.release import Cohort
        with Session(self.engine) as s:
            row = s.exec(select(CohortRow).where(
                CohortRow.tenant_id == self.tenant,
                CohortRow.cohort_id == cohort_id)).first()
            if not row:
                raise NotFoundError(f"cohort {cohort_id}")
            cohort = Cohort.model_validate_json(row.payload)
            cohort.stage = stage
            row.stage = stage
            row.payload = cohort.model_dump_json()
            s.add(row)
            s.commit()

    def list_cohorts(self, agent_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(CohortRow).where(CohortRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(CohortRow.agent_id == agent_id)
            rows = s.exec(q.order_by(CohortRow.id)).all()
            return [{"cohort_id": r.cohort_id, "agent_id": r.agent_id,
                     "stage": r.stage} for r in rows]

    def append_promotion_record(self, record) -> None:
        with Session(self.engine) as s:
            s.add(PromotionRecordRow(
                tenant_id=self.tenant, record_id=record.record_id,
                agent_id=record.agent_id, cohort_id=record.cohort_id,
                kind=record.kind, created_at=_now(),
                payload=record.model_dump_json()))
            s.commit()

    def list_promotion_records(self, agent_id: str | None = None,
                               cohort_id: str | None = None) -> list[dict]:
        import json as _json
        with Session(self.engine) as s:
            q = select(PromotionRecordRow).where(
                PromotionRecordRow.tenant_id == self.tenant)
            if agent_id is not None:
                q = q.where(PromotionRecordRow.agent_id == agent_id)
            if cohort_id is not None:
                q = q.where(PromotionRecordRow.cohort_id == cohort_id)
            rows = s.exec(q.order_by(PromotionRecordRow.id)).all()
            return [_json.loads(r.payload) for r in rows]

    # -- enforcement policies / events / approvals -----------------------------

    def save_policy(self, policy) -> None:
        """Persist a compiled policy (append-only; newest per agent is active)."""
        with Session(self.engine) as s:
            exists = s.exec(select(EnforcementPolicyRow).where(
                EnforcementPolicyRow.tenant_id == self.tenant,
                EnforcementPolicyRow.policy_id == policy.policy_id)).first()
            if exists:
                raise DuplicateVersionError(
                    f"policy {policy.policy_id} already stored")
            s.add(EnforcementPolicyRow(
                tenant_id=self.tenant, policy_id=policy.policy_id,
                agent_id=policy.agent_id, content_hash=policy.content_hash,
                created_at=_now(), payload=policy.model_dump_json()))
            s.commit()

    def get_policy(self, policy_id: str):
        from agenttic.schema.enforcement import EnforcementPolicy
        with Session(self.engine) as s:
            row = s.exec(select(EnforcementPolicyRow).where(
                EnforcementPolicyRow.tenant_id == self.tenant,
                EnforcementPolicyRow.policy_id == policy_id)).first()
            if not row:
                raise NotFoundError(f"policy {policy_id}")
            return EnforcementPolicy.model_validate_json(row.payload)

    def latest_policy(self, agent_id: str):
        from agenttic.schema.enforcement import EnforcementPolicy
        with Session(self.engine) as s:
            row = s.exec(select(EnforcementPolicyRow).where(
                EnforcementPolicyRow.tenant_id == self.tenant,
                EnforcementPolicyRow.agent_id == agent_id
            ).order_by(EnforcementPolicyRow.id.desc())).first()
            if not row:
                raise NotFoundError(f"no policy for agent {agent_id}")
            return EnforcementPolicy.model_validate_json(row.payload)

    def append_enforcement_event(self, event) -> None:
        with Session(self.engine) as s:
            s.add(EnforcementEventRow(
                tenant_id=self.tenant, session_id=event.session_id,
                agent_id=event.agent_id, kind=event.kind,
                action=event.action or "", created_at=_now(),
                payload=event.model_dump_json()))
            s.commit()

    def list_enforcement_events(self, session_id: str | None = None,
                                agent_id: str | None = None) -> list[dict]:
        import json as _json
        with Session(self.engine) as s:
            q = select(EnforcementEventRow).where(
                EnforcementEventRow.tenant_id == self.tenant)
            if session_id is not None:
                q = q.where(EnforcementEventRow.session_id == session_id)
            if agent_id is not None:
                q = q.where(EnforcementEventRow.agent_id == agent_id)
            rows = s.exec(q.order_by(EnforcementEventRow.id)).all()
            return [_json.loads(r.payload) for r in rows]

    def save_approval(self, approval) -> None:
        with Session(self.engine) as s:
            s.add(ApprovalRequestRow(
                tenant_id=self.tenant, approval_id=approval.approval_id,
                session_id=approval.session_id, agent_id=approval.agent_id,
                state=approval.state, created_at=_now(),
                payload=approval.model_dump_json()))
            s.commit()

    def get_approval(self, approval_id: str):
        from agenttic.schema.enforcement import ApprovalRequest
        with Session(self.engine) as s:
            row = s.exec(select(ApprovalRequestRow).where(
                ApprovalRequestRow.tenant_id == self.tenant,
                ApprovalRequestRow.approval_id == approval_id)).first()
            if not row:
                raise NotFoundError(f"approval {approval_id}")
            return ApprovalRequest.model_validate_json(row.payload)

    def update_approval(self, approval) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(ApprovalRequestRow).where(
                ApprovalRequestRow.tenant_id == self.tenant,
                ApprovalRequestRow.approval_id == approval.approval_id)).first()
            if not row:
                raise NotFoundError(f"approval {approval.approval_id}")
            row.state = approval.state
            row.payload = approval.model_dump_json()
            s.add(row)
            s.commit()

    def list_approvals(self, session_id: str | None = None,
                       state: str | None = None) -> list[dict]:
        import json as _json
        with Session(self.engine) as s:
            q = select(ApprovalRequestRow).where(
                ApprovalRequestRow.tenant_id == self.tenant)
            if session_id is not None:
                q = q.where(ApprovalRequestRow.session_id == session_id)
            if state is not None:
                q = q.where(ApprovalRequestRow.state == state)
            rows = s.exec(q.order_by(ApprovalRequestRow.id)).all()
            return [_json.loads(r.payload) for r in rows]

    # -- agent cards (append-only, versioned) ----------------------------------

    def save_card(self, card) -> None:
        """Persist a card version. Append-only: re-saving (agent_id, version) raises.
        If version is 1 and a card already exists, auto-bumps to the next version."""
        with Session(self.engine) as s:
            existing = s.exec(select(AgentCardRow).where(
                AgentCardRow.tenant_id == self.tenant,
                AgentCardRow.agent_id == card.agent_id
            ).order_by(AgentCardRow.version.desc())).first()
            version = card.version
            if existing is not None and version <= existing.version:
                version = existing.version + 1
                card = card.model_copy(update={"version": version})
            s.add(AgentCardRow(
                tenant_id=self.tenant, agent_id=card.agent_id, version=version,
                source=card.source, created_at=_now(),
                payload=card.model_dump_json()))
            s.commit()

    def get_card(self, agent_id: str, version: int | None = None):
        from agenttic.schema.agent_card import AgentCard
        with Session(self.engine) as s:
            q = select(AgentCardRow).where(
                AgentCardRow.tenant_id == self.tenant,
                AgentCardRow.agent_id == agent_id)
            q = q.where(AgentCardRow.version == version) if version is not None \
                else q.order_by(AgentCardRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"card {agent_id} v{version}")
            return AgentCard.model_validate_json(row.payload)

    def list_cards(self, source: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(AgentCardRow).where(AgentCardRow.tenant_id == self.tenant)
            if source is not None:
                q = q.where(AgentCardRow.source == source)
            rows = s.exec(q.order_by(AgentCardRow.agent_id,
                                     AgentCardRow.version)).all()
            # latest version per agent
            latest: dict[str, dict] = {}
            for r in rows:
                latest[r.agent_id] = {"agent_id": r.agent_id, "version": r.version,
                                      "source": r.source}
            return list(latest.values())

    # -- elicitation summaries (append-only) -----------------------------------

    def save_elicitation_summary(self, agent_id: str, summary: dict) -> None:
        import json as _json
        with Session(self.engine) as s:
            s.add(ElicitationSummaryRow(
                tenant_id=self.tenant, agent_id=agent_id,
                inconsistent=bool(summary.get("inconsistent")),
                underpowered=bool(summary.get("underpowered")),
                created_at=_now(), payload=_json.dumps(summary)))
            s.commit()

    def list_elicitation_summaries(self, agent_id: str) -> list[dict]:
        import json as _json
        with Session(self.engine) as s:
            rows = s.exec(select(ElicitationSummaryRow).where(
                ElicitationSummaryRow.tenant_id == self.tenant,
                ElicitationSummaryRow.agent_id == agent_id
            ).order_by(ElicitationSummaryRow.id)).all()
            return [_json.loads(r.payload) for r in rows]

    def latest_elicitation_summary(self, agent_id: str) -> dict | None:
        import json as _json
        with Session(self.engine) as s:
            row = s.exec(select(ElicitationSummaryRow).where(
                ElicitationSummaryRow.tenant_id == self.tenant,
                ElicitationSummaryRow.agent_id == agent_id
            ).order_by(ElicitationSummaryRow.id.desc())).first()
            return _json.loads(row.payload) if row else None

    # -- spend ledger (budget caps) --------------------------------------------

    def record_spend(self, model: str, cost_usd: float) -> None:
        if not cost_usd:
            return
        now = _now()
        with Session(self.engine) as s:
            s.add(SpendRow(tenant_id=self.tenant, day=now.strftime("%Y-%m-%d"),
                           model=model, cost_usd=cost_usd, created_at=now))
            s.commit()

    def spend_today(self) -> float:
        return self.spend_since_days(0)

    def spend_since_days(self, days: int) -> float:
        """Total spend over the trailing ``days`` days (0 => just today)."""
        from datetime import timedelta
        start = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with Session(self.engine) as s:
            total = s.exec(select(func.sum(SpendRow.cost_usd)).where(
                SpendRow.tenant_id == self.tenant, SpendRow.day >= start)).one()
            return float(total or 0.0)

    # -- retention --------------------------------------------------------------

    def prune_traces(self, older_than_days: int) -> int:
        """Delete trace rows (this tenant) older than ``older_than_days``.
        Returns the number removed. Live + batch alike; scorecards keep their
        aggregates, so historical results survive."""
        if older_than_days <= 0:
            return 0
        from datetime import timedelta
        cutoff = _now() - timedelta(days=older_than_days)
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.created_at < cutoff)).all()
            for r in rows:
                s.delete(r)
            s.commit()
            return len(rows)

    def redact_old_traces(self, older_than_days: int) -> int:
        """Strip span inputs/outputs and final_output from traces (this tenant)
        older than ``older_than_days`` — a PII control that keeps the trace row
        (timing/cost/structure) while dropping the potentially-sensitive
        payloads. Returns the number redacted. Idempotent."""
        if older_than_days <= 0:
            return 0
        from datetime import timedelta
        cutoff = _now() - timedelta(days=older_than_days)
        n = 0
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.created_at < cutoff)).all()
            for r in rows:
                p = json.loads(r.payload)
                for span in p.get("spans", []):
                    span["input"] = {}
                    span["output"] = {}
                    if span.get("error"):
                        span["error"] = "[redacted]"
                if p.get("final_output"):
                    p["final_output"] = "[redacted]"
                r.payload = json.dumps(p)
                s.add(r)
                n += 1
            s.commit()
            return n
