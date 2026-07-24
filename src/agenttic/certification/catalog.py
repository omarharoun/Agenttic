"""Catalog conformance — the promotion gate (SPEC-12 Step 58, M39).

Steps 54–57 make individual subjects attestable: an agent, an MCP server, a tool,
a memory store. This step is what an organisation running more than one of them
actually needs — a register of what is approved for use, and a rule about how
something gets into it and how it leaves.

The register is worth exactly as much as the rule, so the rule is enforced in
code rather than described in a policy document:

**Promotion refuses by default.** :func:`promote` raises unless the subject's
signed manifest verifies, has not expired, has not been revoked, and a *named
human* supplied a rationale. There is no "force" argument. A catalog that can be
appended to without evidence is a spreadsheet.

**A challenger must be shadowed before it replaces an incumbent.**
:func:`shadow_compare` runs both against identical stimulus and reports
regressions — cases the incumbent handled and the challenger did not. Promotion
over an incumbent refuses while unexplained regressions stand.

**Retirement cascades.** An agent is not independent of the tools, servers and
memory it was certified with. :func:`retire` marks every promoted dependent as
requiring re-verification and suspends its manifest on the revocation list —
because the alternative is a catalog that says an agent is approved on the
strength of a component that was withdrawn.

**Conformance is a question you can ask the catalog.**
:func:`check_conformance` walks the register and reports every entry whose
evidence has lapsed, been revoked, or rests on a dependency that is not itself
promoted. It reports; it never quietly repairs.

The exported document is canonical and hashable, so a catalog can itself be
signed as evidence (Step 54) — which is the point at which "our approved agent
list" becomes something an auditor can check rather than take on trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal

from agenttic.schema.attestation import (
    RevocationList, SignedManifest, content_hash)

#: What a catalog entry can be. Agents and their supply chain share one register
#: on purpose — the dependency edges only mean something if both ends are in it.
SubjectKind = Literal["agent", "tool", "mcp_server", "memory"]

#: Lifecycle. `candidate` is registered-with-evidence; `shadow` is running
#: against production stimulus beside an incumbent; `promoted` is approved for
#: use; `retired` is withdrawn. `needs_reverification` is promoted-but-disturbed:
#: still in use, no longer trusted, and deliberately noisy.
EntryStatus = Literal[
    "candidate", "shadow", "promoted", "needs_reverification", "retired"]

_TERMINAL = ("retired",)


class PromotionRefused(RuntimeError):
    """Raised when promotion is attempted without the evidence to support it.

    Deliberately an exception rather than a boolean: a caller has to handle it,
    and a caller that ignores it fails loudly instead of writing an unsupported
    entry into the register."""


def _utc(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# entries and records
# --------------------------------------------------------------------------- #

@dataclass
class CatalogEntry:
    """One subject in the register, at one version."""

    subject_id: str
    kind: SubjectKind
    version: str = ""
    manifest_id: str = ""
    manifest_sha256: str = ""
    scorecard_sha256: str = ""
    score: float | None = None
    status: EntryStatus = "candidate"
    #: subject_ids this entry was certified WITH. An agent depends on its tools,
    #: servers and memory; those dependencies are what makes retirement cascade.
    depends_on: tuple[str, ...] = ()
    recorded_at: datetime = field(default_factory=lambda: _utc())
    notes: str = ""

    @property
    def ref(self) -> str:
        """Stable identity of this entry: subject at a version."""
        return f"{self.kind}:{self.subject_id}@{self.version or '-'}"

    def as_dict(self) -> dict:
        return {
            "ref": self.ref, "subject_id": self.subject_id, "kind": self.kind,
            "version": self.version, "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "scorecard_sha256": self.scorecard_sha256,
            "score": self.score, "status": self.status,
            "depends_on": list(self.depends_on),
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
            "notes": self.notes,
        }


@dataclass
class ShadowReport:
    """A challenger run beside an incumbent on identical stimulus.

    ``regressions`` is the number that decides a promotion: cases the incumbent
    handled and the challenger did not. An aggregate score that improved while
    three previously-working cases broke is a worse deployment, and an average
    hides that.
    """

    incumbent_ref: str
    challenger_ref: str
    n_cases: int = 0
    agreements: int = 0
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    incumbent_score: float = 0.0
    challenger_score: float = 0.0

    @property
    def agreement_rate(self) -> float:
        return self.agreements / self.n_cases if self.n_cases else 0.0

    @property
    def clean(self) -> bool:
        """No case got worse. Not "the average went up"."""
        return self.n_cases > 0 and not self.regressions

    def as_dict(self) -> dict:
        return {
            "incumbent": self.incumbent_ref, "challenger": self.challenger_ref,
            "n_cases": self.n_cases, "agreement_rate": round(self.agreement_rate, 4),
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
            "incumbent_score": round(self.incumbent_score, 4),
            "challenger_score": round(self.challenger_score, 4),
            "clean": self.clean,
        }


@dataclass
class PromotionRecord:
    """Why something was approved, and on whose authority. Append-only."""

    entry_ref: str
    from_status: EntryStatus
    to_status: EntryStatus
    approver: str
    rationale: str
    manifest_id: str = ""
    manifest_status: str = ""
    shadow: dict | None = None
    recorded_at: datetime = field(default_factory=lambda: _utc())

    def as_dict(self) -> dict:
        return {
            "kind": "promotion", "entry_ref": self.entry_ref,
            "from_status": self.from_status, "to_status": self.to_status,
            "approver": self.approver, "rationale": self.rationale,
            "manifest_id": self.manifest_id,
            "manifest_status": self.manifest_status, "shadow": self.shadow,
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
        }


@dataclass
class RetirementRecord:
    """Withdrawal, and everything it disturbed."""

    entry_ref: str
    reason: str
    approver: str
    replaced_by: str = ""
    #: refs of promoted entries moved to needs_reverification by this retirement
    cascaded_to: list[str] = field(default_factory=list)
    recorded_at: datetime = field(default_factory=lambda: _utc())

    def as_dict(self) -> dict:
        return {
            "kind": "retirement", "entry_ref": self.entry_ref,
            "reason": self.reason, "approver": self.approver,
            "replaced_by": self.replaced_by, "cascaded_to": list(self.cascaded_to),
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
        }


@dataclass
class ConformanceFinding:
    entry_ref: str
    problem: str
    detail: str
    severity: Literal["error", "warning"] = "error"

    def as_dict(self) -> dict:
        return {"entry_ref": self.entry_ref, "problem": self.problem,
                "detail": self.detail, "severity": self.severity}


# --------------------------------------------------------------------------- #
# the catalog
# --------------------------------------------------------------------------- #

class Catalog:
    """An append-only register of certified subjects.

    Entries are mutable in *status* only, and every status change leaves a record
    behind. Nothing is ever deleted: a retired entry stays visible, because "we
    used to approve this" is exactly the question an incident asks.
    """

    def __init__(self, owner: str = "agenttic"):
        self.owner = owner
        self._entries: dict[str, CatalogEntry] = {}
        self.records: list[PromotionRecord | RetirementRecord] = []

    # -- registration -------------------------------------------------------- #

    def register(self, entry: CatalogEntry) -> CatalogEntry:
        """Add a subject as a *candidate*. Registration is not approval."""
        if entry.status == "promoted":
            raise PromotionRefused(
                f"{entry.ref} cannot be registered directly as 'promoted' — "
                "promotion requires evidence and a named approver; use promote()")
        self._entries[entry.ref] = entry
        return entry

    def get(self, ref: str) -> CatalogEntry | None:
        return self._entries.get(ref)

    @property
    def entries(self) -> list[CatalogEntry]:
        return sorted(self._entries.values(), key=lambda e: e.ref)

    def by_status(self, status: EntryStatus) -> list[CatalogEntry]:
        return [e for e in self.entries if e.status == status]

    def promoted(self) -> list[CatalogEntry]:
        return self.by_status("promoted")

    def dependents_of(self, ref: str) -> list[CatalogEntry]:
        """Entries certified WITH this subject. Retirement walks these."""
        target = self._entries.get(ref)
        if target is None:
            return []
        keys = {ref, target.subject_id}
        return [e for e in self.entries if keys & set(e.depends_on)]

    # -- promotion ----------------------------------------------------------- #

    def promote(
        self, ref: str, *, approver: str, rationale: str,
        signed: SignedManifest | None = None,
        scorecard: dict | None = None,
        revocations: RevocationList | None = None,
        shadow: ShadowReport | None = None,
        incumbent_ref: str | None = None,
        now: datetime | None = None,
    ) -> PromotionRecord:
        """Approve a subject for use, or refuse and say precisely why.

        Every refusal below is a real way an approved-agent register goes wrong in
        practice, so each one names the specific missing thing rather than
        returning a generic failure."""
        now = _utc(now)
        entry = self._entries.get(ref)
        if entry is None:
            raise PromotionRefused(f"{ref} is not registered in this catalog")
        if entry.status in _TERMINAL:
            raise PromotionRefused(
                f"{ref} is {entry.status} — a withdrawn entry is not promoted "
                "back; register the new version as its own entry")
        if not approver.strip():
            raise PromotionRefused(
                f"{ref} requires a NAMED approver — an approval nobody signed is "
                "not an approval")
        if not rationale.strip():
            raise PromotionRefused(
                f"{ref} requires a rationale — the register has to record why "
                "this was considered good enough, not merely that it was")
        if signed is None:
            raise PromotionRefused(
                f"{ref} has no signed evidence manifest; promotion requires "
                "evidence, and unsigned evidence is an assertion")

        from agenttic.certification.attest import verify_manifest
        result = verify_manifest(signed, scorecard=scorecard,
                                 revocations=revocations, now=now)
        if not result.ok:
            raise PromotionRefused(
                f"{ref} evidence is {result.status}: {result.reason}")

        # a challenger displacing an incumbent must have been shadowed clean
        if incumbent_ref:
            if shadow is None:
                raise PromotionRefused(
                    f"{ref} would replace {incumbent_ref} without a shadow "
                    "comparison — a challenger is promoted on evidence that it "
                    "does not break what already works")
            if shadow.challenger_ref != ref or shadow.incumbent_ref != incumbent_ref:
                raise PromotionRefused(
                    f"shadow report is for {shadow.challenger_ref} vs "
                    f"{shadow.incumbent_ref}, not {ref} vs {incumbent_ref}")
            if not shadow.clean:
                raise PromotionRefused(
                    f"{ref} regressed {len(shadow.regressions)} case(s) the "
                    f"incumbent handled ({', '.join(shadow.regressions[:3])}) — "
                    "an improved average does not cover a case that got worse")

        rec = PromotionRecord(
            entry_ref=ref, from_status=entry.status, to_status="promoted",
            approver=approver, rationale=rationale,
            manifest_id=signed.manifest.manifest_id,
            manifest_status=result.status,
            shadow=shadow.as_dict() if shadow else None, recorded_at=now)
        entry.status = "promoted"
        entry.manifest_id = signed.manifest.manifest_id
        entry.manifest_sha256 = signed.manifest_sha256
        entry.scorecard_sha256 = signed.manifest.scorecard_hash
        self.records.append(rec)

        if incumbent_ref and (inc := self._entries.get(incumbent_ref)):
            if inc.status == "promoted":
                inc.status = "candidate"
                inc.notes = (inc.notes + " " if inc.notes else "") + \
                    f"superseded by {ref}"
        return rec

    def start_shadow(self, ref: str, *, now: datetime | None = None) -> CatalogEntry:
        """Move a candidate into shadow mode — running beside the incumbent,
        graded, and not yet serving anything."""
        entry = self._entries.get(ref)
        if entry is None:
            raise PromotionRefused(f"{ref} is not registered in this catalog")
        if entry.status in _TERMINAL:
            raise PromotionRefused(f"{ref} is {entry.status}")
        entry.status = "shadow"
        entry.recorded_at = _utc(now)
        return entry

    # -- retirement ---------------------------------------------------------- #

    def retire(
        self, ref: str, *, reason: str, approver: str, replaced_by: str = "",
        revocations: RevocationList | None = None,
        now: datetime | None = None,
    ) -> RetirementRecord:
        """Withdraw a subject and disturb everything that depended on it.

        The cascade is the whole reason this lives in the catalog rather than in
        each subject: retiring an MCP server has to reach the agents certified
        against it, and no single subject knows who those are."""
        now = _utc(now)
        entry = self._entries.get(ref)
        if entry is None:
            raise PromotionRefused(f"{ref} is not registered in this catalog")
        if not reason.strip() or not approver.strip():
            raise PromotionRefused(
                f"{ref} retirement requires a named approver and a reason")

        cascaded: list[str] = []
        for dep in self.dependents_of(ref):
            if dep.ref == ref or dep.status in _TERMINAL:
                continue
            if dep.status in ("promoted", "shadow"):
                dep.status = "needs_reverification"
                dep.notes = (dep.notes + " " if dep.notes else "") + \
                    f"dependency {ref} retired: {reason}"
                cascaded.append(dep.ref)
                if revocations is not None and dep.manifest_id:
                    from agenttic.certification.attest import append_revocation
                    append_revocation(
                        revocations, manifest_id=dep.manifest_id,
                        subject_config_hash=dep.scorecard_sha256 or dep.manifest_sha256,
                        status="suspended",
                        reason=f"dependency {ref} retired: {reason}",
                        source="catalog:retire_cascade", now=now)

        if revocations is not None and entry.manifest_id:
            from agenttic.certification.attest import append_revocation
            append_revocation(
                revocations, manifest_id=entry.manifest_id,
                subject_config_hash=entry.scorecard_sha256 or entry.manifest_sha256,
                status="revoked", reason=reason, source="catalog:retire", now=now)

        entry.status = "retired"
        entry.notes = (entry.notes + " " if entry.notes else "") + f"retired: {reason}"
        rec = RetirementRecord(entry_ref=ref, reason=reason, approver=approver,
                               replaced_by=replaced_by, cascaded_to=cascaded,
                               recorded_at=now)
        self.records.append(rec)
        return rec

    # -- conformance --------------------------------------------------------- #

    def check_conformance(
        self, *, manifests: dict[str, SignedManifest] | None = None,
        revocations: RevocationList | None = None,
        now: datetime | None = None,
    ) -> list[ConformanceFinding]:
        """Ask the register whether it still means what it says.

        Reports, never repairs: silently downgrading an entry would hide the
        window during which something was approved on lapsed evidence."""
        now = _utc(now)
        manifests = manifests or {}
        out: list[ConformanceFinding] = []
        from agenttic.certification.attest import verify_manifest

        for e in self.entries:
            if e.status == "retired":
                continue
            if e.status == "needs_reverification":
                out.append(ConformanceFinding(
                    e.ref, "needs_reverification",
                    f"in use but disturbed: {e.notes.strip()}", "error"))
                continue
            if e.status != "promoted":
                continue

            if not e.manifest_id:
                out.append(ConformanceFinding(
                    e.ref, "no_evidence",
                    "promoted with no manifest recorded", "error"))
                continue

            signed = manifests.get(e.manifest_id)
            if signed is None:
                out.append(ConformanceFinding(
                    e.ref, "evidence_unavailable",
                    f"manifest {e.manifest_id} is referenced but was not supplied "
                    "for checking — an unverifiable reference is not evidence",
                    "warning"))
            else:
                if signed.manifest_sha256 != e.manifest_sha256:
                    out.append(ConformanceFinding(
                        e.ref, "evidence_mismatch",
                        f"the manifest supplied for {e.manifest_id} does not hash "
                        "to the one recorded at promotion", "error"))
                res = verify_manifest(signed, revocations=revocations, now=now)
                if not res.ok:
                    out.append(ConformanceFinding(
                        e.ref, f"evidence_{res.status}", res.reason, "error"))

            for dep_id in e.depends_on:
                dep = next((d for d in self.entries
                            if d.ref == dep_id or d.subject_id == dep_id), None)
                if dep is None:
                    out.append(ConformanceFinding(
                        e.ref, "unregistered_dependency",
                        f"promoted while depending on {dep_id}, which is not in "
                        "the catalog at all", "error"))
                elif dep.status != "promoted":
                    out.append(ConformanceFinding(
                        e.ref, "uncertified_dependency",
                        f"promoted while its dependency {dep.ref} is "
                        f"'{dep.status}' — the agent is approved on the strength "
                        "of a component that is not", "error"))
        return out

    # -- export -------------------------------------------------------------- #

    def export(self, *, now: datetime | None = None) -> dict:
        """The conformance catalog as a canonical, hashable document."""
        return {
            "catalog_version": 1,
            "owner": self.owner,
            "exported_at": _utc(now).isoformat(),
            "entries": [e.as_dict() for e in self.entries],
            "records": [r.as_dict() for r in self.records],
            "counts": {
                s: len(self.by_status(s))                     # type: ignore[arg-type]
                for s in ("candidate", "shadow", "promoted",
                          "needs_reverification", "retired")
            },
        }

    def export_sha256(self, *, now: datetime | None = None) -> str:
        doc = self.export(now=now)
        return content_hash(doc)

    @classmethod
    def from_export(cls, doc: dict) -> "Catalog":
        """Rebuild a catalog from its exported document.

        Needed for the thing an export is actually for: checking someone else's
        register — in CI, or as an auditor — without their process. Entries come
        back at the status they were exported at, bypassing :meth:`register`'s
        refusal, because this is reconstruction of a recorded fact rather than a
        new approval.
        """
        cat = cls(owner=doc.get("owner", "agenttic"))
        for e in doc.get("entries", []):
            recorded = e.get("recorded_at")
            entry = CatalogEntry(
                subject_id=e["subject_id"], kind=e["kind"],
                version=e.get("version", ""),
                manifest_id=e.get("manifest_id", ""),
                manifest_sha256=e.get("manifest_sha256", ""),
                scorecard_sha256=e.get("scorecard_sha256", ""),
                score=e.get("score"), status=e.get("status", "candidate"),
                depends_on=tuple(e.get("depends_on") or ()),
                recorded_at=(datetime.fromisoformat(recorded) if recorded
                             else _utc()),
                notes=e.get("notes", ""))
            cat._entries[entry.ref] = entry
        for r in doc.get("records", []):
            ts = r.get("recorded_at")
            when = datetime.fromisoformat(ts) if ts else _utc()
            if r.get("kind") == "retirement":
                cat.records.append(RetirementRecord(
                    entry_ref=r["entry_ref"], reason=r.get("reason", ""),
                    approver=r.get("approver", ""),
                    replaced_by=r.get("replaced_by", ""),
                    cascaded_to=list(r.get("cascaded_to") or []),
                    recorded_at=when))
            else:
                cat.records.append(PromotionRecord(
                    entry_ref=r["entry_ref"],
                    from_status=r.get("from_status", "candidate"),
                    to_status=r.get("to_status", "promoted"),
                    approver=r.get("approver", ""),
                    rationale=r.get("rationale", ""),
                    manifest_id=r.get("manifest_id", ""),
                    manifest_status=r.get("manifest_status", ""),
                    shadow=r.get("shadow"), recorded_at=when))
        return cat


# --------------------------------------------------------------------------- #
# shadow mode
# --------------------------------------------------------------------------- #

def shadow_compare(
    incumbent_ref: str, challenger_ref: str,
    cases: Iterable[tuple[str, float, float]], *, threshold: float = 0.5,
) -> ShadowReport:
    """Compare a challenger against an incumbent on IDENTICAL stimulus.

    ``cases`` is ``(case_id, incumbent_score, challenger_score)``. A case counts
    as a regression when the incumbent was at or above ``threshold`` and the
    challenger is not — per case, deliberately, because the failure mode this
    exists to catch is an average that improves while specific behaviour breaks.
    """
    rep = ShadowReport(incumbent_ref=incumbent_ref, challenger_ref=challenger_ref)
    inc_total = chal_total = 0.0
    for case_id, inc, chal in cases:
        rep.n_cases += 1
        inc_total += inc
        chal_total += chal
        inc_ok, chal_ok = inc >= threshold, chal >= threshold
        if inc_ok == chal_ok:
            rep.agreements += 1
        elif inc_ok and not chal_ok:
            rep.regressions.append(case_id)
        else:
            rep.improvements.append(case_id)
    if rep.n_cases:
        rep.incumbent_score = inc_total / rep.n_cases
        rep.challenger_score = chal_total / rep.n_cases
    return rep


__all__ = [
    "Catalog", "CatalogEntry", "ConformanceFinding", "PromotionRecord",
    "PromotionRefused", "RetirementRecord", "ShadowReport", "shadow_compare",
]
