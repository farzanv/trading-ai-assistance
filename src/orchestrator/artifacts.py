"""Validation of the four v2 external artifacts and persistence safety.

Every model- or gate-produced artifact passes JSON Schema validation plus the
semantic checks the schema cannot express — binding to the immutable lane
identity and the verified candidate revision, exact-set reconciliation,
lifecycle legality, verdict/checklist consistency (architecture §7.3, §8) —
before it can become a typed event. A failure raises :class:`ArtifactError`;
the coordinator turns that into one retry, then a STOP.

Persistence safety (design §7.1, architecture §7.4): structured artifacts are
rejected when they carry credential-named fields or credential-shaped values,
diagnostic text is bounded to 1 MiB per invocation, and error evidence is
sanitized (schema paths and keywords only — never raw instance values, which
could echo a secret into the ledger).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from orchestrator.model import (
    AuthorResultAccepted,
    BLOCKING_SEVERITIES,
    Disposition,
    FindingState,
    FoldAccepted,
    GateCategory,
    GuidanceAccepted,
    LaneIdentity,
    LEGAL_PRIOR_OUTCOMES,
    Lens,
    NewFinding,
    PriorAssessment,
    PriorOutcome,
    ReviewAccepted,
    Severity,
)

DIAGNOSTIC_MAX_BYTES = 1_048_576  # 1 MiB per invocation (operator ruling 2026-08-31)

_SCHEMA_FILES = {
    "author-result": "author-result.schema.json",
    "fold": "fold.schema.json",
    "review": "review.schema.json",
    "target-gate-result": "target-gate-result.schema.json",
}

#: Key segments that make a structured artifact forbidden (obvious credential
#: fields). Matched on whole underscore-separated segments and whole keys, so
#: ``tokens`` (usage counts) and ``author`` are not false positives.
_FORBIDDEN_KEY_SEGMENTS = frozenset(
    {"password", "passwd", "secret", "secrets", "apikey", "token", "credential", "credentials", "dsn"}
)
_FORBIDDEN_WHOLE_KEYS = frozenset(
    {"api_key", "auth_file", "authorization", "connection_string", "access_key", "private_key", "env"}
)
#: Schema-approved field names that would otherwise trip the segment filter.
#: Each entry names a published v2 contract field whose NAME contains a
#: forbidden segment but whose VALUE is a structural check result, never a
#: secret (the security checklist of design §7.1).
_SCHEMA_APPROVED_KEYS = frozenset({"no_credential_logging"})

#: Values that look like credential material: URL userinfo passwords,
#: key=value connection-string/token assignments (ODBC/libpq style), bearer
#: tokens, and private-key markers.
_FORBIDDEN_VALUE_RES = (
    re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^@\s]+@"),
    re.compile(
        r"(?i)\b(pwd|password|passwd|secret|token|apikey|api_key|accesskey|access_key|"
        r"client_secret|clientsecret|sslpassword)\s*=\s*[^;\s]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)
_PRIVATE_KEY_MARKER = "-----BEGIN "

_ACCEPTING_GATE_CATEGORIES = frozenset(
    {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
)


class ArtifactError(Exception):
    """The artifact is malformed, contradictory, unbound, or unsafe to persist.

    ``errors`` is ledger-safe: schema failures are reported as path + keyword
    only, and semantic messages never embed raw artifact string values.
    """

    def __init__(self, artifact: str, errors: list[str]) -> None:
        self.artifact = artifact
        self.errors = errors
        super().__init__(f"{artifact}: " + "; ".join(errors))


@dataclass(frozen=True)
class SchemaSet:
    validators: Mapping[str, Draft202012Validator]
    digest: str  # sha256 over the sorted (name, file-digest) pairs


def load_schema_set(schemas_dir: Path) -> SchemaSet:
    validators: dict[str, Draft202012Validator] = {}
    file_digests: list[tuple[str, str]] = []
    for name, filename in sorted(_SCHEMA_FILES.items()):
        content = (schemas_dir / filename).read_bytes()
        schema = json.loads(content.decode("utf-8"))
        Draft202012Validator.check_schema(schema)
        validators[name] = Draft202012Validator(schema)
        file_digests.append((name, hashlib.sha256(content).hexdigest()))
    canonical = json.dumps(file_digests, sort_keys=True, separators=(",", ":"))
    return SchemaSet(
        validators=validators, digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


def _schema_validate(schemas: SchemaSet, artifact: str, raw: Mapping[str, Any]) -> None:
    # Sanitized: path + failed keyword only. jsonschema's message text embeds
    # instance values, which must never reach the ledger.
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.validator}"
        for e in schemas.validators[artifact].iter_errors(raw)
    ]
    if errors:
        raise ArtifactError(artifact, errors)


def artifact_digest(raw: Mapping[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Persistence safety
# ---------------------------------------------------------------------------


def check_persistence_safety(artifact: str, raw: Any, _path: str = "") -> None:
    """Reject credential-named fields and credential-shaped values (fail closed)."""
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            key_path = f"{_path}/{key}"
            lowered = str(key).lower()
            if lowered not in _SCHEMA_APPROVED_KEYS and (
                lowered in _FORBIDDEN_WHOLE_KEYS
                or (set(lowered.split("_")) & _FORBIDDEN_KEY_SEGMENTS)
            ):
                raise ArtifactError(artifact, [f"forbidden credential-named field: {key_path}"])
            check_persistence_safety(artifact, value, key_path)
    elif isinstance(raw, (list, tuple)):
        for index, item in enumerate(raw):
            check_persistence_safety(artifact, item, f"{_path}[{index}]")
    elif isinstance(raw, str):
        for pattern in _FORBIDDEN_VALUE_RES:
            if pattern.search(raw):
                raise ArtifactError(artifact, [f"credential-shaped value at {_path}"])
        if _PRIVATE_KEY_MARKER in raw:
            raise ArtifactError(artifact, [f"key material marker at {_path}"])


def bounded_diagnostic(text: str) -> str:
    """Cap diagnostic text at 1 MiB; a truncation names the digest and reason."""
    encoded = text.encode("utf-8")
    if len(encoded) <= DIAGNOSTIC_MAX_BYTES:
        return text
    digest = hashlib.sha256(encoded).hexdigest()
    marker = f"\n[TRUNCATED reason=diagnostic-bound original_bytes={len(encoded)} sha256={digest}]"
    keep = DIAGNOSTIC_MAX_BYTES - len(marker.encode("utf-8"))
    truncated = encoded[:keep].decode("utf-8", errors="ignore")
    return truncated + marker


# ---------------------------------------------------------------------------
# Typed validation results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorResultSummary:
    commit: str
    revision: int
    tree_digest: str
    has_unknown_contracts: bool


@dataclass(frozen=True)
class FoldSummary:
    commit: str
    revision: int
    tree_digest: str
    dispositions: tuple[tuple[str, Disposition], ...]
    has_unknown_contracts: bool


@dataclass(frozen=True)
class ReviewSummary:
    lens: Lens
    verdict: str
    tree_digest: str
    has_scope_observations: bool
    new_findings: tuple[NewFinding, ...]
    prior_findings: tuple[PriorAssessment, ...]
    p3_findings: tuple[tuple[str, str], ...]  # (id, title) for the backlog artifact


@dataclass(frozen=True)
class GuidanceSummary:
    finding_ids: tuple[str, ...]


@dataclass(frozen=True)
class GateSummary:
    category: GateCategory
    verdict: str
    failed_checks: tuple[str, ...]


# ---------------------------------------------------------------------------
# Validators (schema + identity binding + semantics)
# ---------------------------------------------------------------------------


def validate_author_result(
    schemas: SchemaSet,
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    expected_revision: int,
    expected_author_agent: str,
) -> AuthorResultSummary:
    check_persistence_safety("author-result", raw)
    _schema_validate(schemas, "author-result", raw)
    errors: list[str] = []
    if raw["work_item"] != identity.work_item:
        errors.append("work_item does not match the authorized lane")
    if raw["scope_base"] != identity.scope_base:
        errors.append("scope_base does not match the authorized lane")
    if raw["revision"] != expected_revision:
        errors.append(f"revision {raw['revision']} != expected {expected_revision}")
    if raw["commit"] == identity.scope_base:
        errors.append("commit equals scope_base (no new revision)")
    if raw["author"]["agent"] != expected_author_agent:
        errors.append("author agent is not the lane's authorized author")
    if errors:
        raise ArtifactError("author-result", errors)
    return AuthorResultSummary(
        commit=raw["commit"],
        revision=raw["revision"],
        tree_digest=raw["tree_digest"],
        has_unknown_contracts=bool(raw.get("unknown_contracts")),
    )


def validate_fold(
    schemas: SchemaSet,
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    outstanding_ids: frozenset[str],
    expected_revision: int,
    prev_sha: str,
    expected_author_agent: str,
) -> FoldSummary:
    check_persistence_safety("fold", raw)
    _schema_validate(schemas, "fold", raw)
    errors: list[str] = []
    if raw["revision"] != expected_revision:
        errors.append(f"revision {raw['revision']} != expected {expected_revision}")
    if raw["folded_review_range"] != f"{identity.scope_base}..{prev_sha}":
        errors.append("folded_review_range does not match the reviewed lane range")
    if raw["commit"] in {prev_sha, identity.scope_base}:
        errors.append("commit is not a new revision")
    if raw["author"]["agent"] != expected_author_agent:
        errors.append("author agent is not the lane's authorized author")
    seen: list[str] = [d["finding_id"] for d in raw["dispositions"]]
    duplicates = sorted({fid for fid in seen if seen.count(fid) > 1})
    if duplicates:
        errors.append(f"duplicate dispositions: {duplicates}")
    missing = sorted(outstanding_ids - set(seen))
    if missing:
        errors.append(f"missing dispositions for outstanding findings: {missing}")
    unknown = sorted(set(seen) - outstanding_ids)
    if unknown:
        errors.append(f"dispositions for unknown/non-outstanding findings: {unknown}")
    if errors:
        raise ArtifactError("fold", errors)
    dispositions = tuple(
        (d["finding_id"], Disposition(d["disposition"])) for d in raw["dispositions"]
    )
    return FoldSummary(
        commit=raw["commit"],
        revision=raw["revision"],
        tree_digest=raw["tree_digest"],
        dispositions=dispositions,
        has_unknown_contracts=bool(raw.get("unknown_contracts")),
    )


def _check_review_binding(
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    current_sha: str,
    current_tree: str,
    errors: list[str],
) -> None:
    if raw["reviewed_range"] != f"{identity.scope_base}..{current_sha}":
        errors.append("reviewed_range does not match the verified lane range")
    if raw["tree_digest"] != current_tree:
        errors.append("tree_digest does not match the verified revision")
    if raw["manifest"] != identity.manifest:
        errors.append("manifest does not match the authorized lane")


def validate_review(
    schemas: SchemaSet,
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    expected_lens: Lens,
    historical: Mapping[str, FindingState],
    current_sha: str,
    current_tree: str,
    expected_review_kind: str,
    expected_reviewer_agent: str,
    handoff_authorized: bool = False,
) -> ReviewSummary:
    check_persistence_safety("review", raw)
    _schema_validate(schemas, "review", raw)
    errors: list[str] = []
    _check_review_binding(raw, identity, current_sha, current_tree, errors)
    if raw["lens"] != expected_lens.value:
        errors.append(f"lens {raw['lens']!r} != expected {expected_lens.value!r}")
    if raw["review_kind"] != expected_review_kind:
        errors.append(
            f"review_kind {raw['review_kind']!r} != lane's {expected_review_kind!r}"
        )
    if raw["reviewer"]["agent"] != expected_reviewer_agent:
        # Structural non-authoring review: only the lane's authorized
        # non-authoring reviewer can gate; author self-review never validates.
        errors.append("reviewer agent is not the lane's authorized reviewer")
    if raw.get("handoff") is not None and not handoff_authorized:
        errors.append("handoff provenance is not authorized for this lane")

    finding_ids = [f["id"] for f in raw["findings"]]
    duplicate_new = sorted({fid for fid in finding_ids if finding_ids.count(fid) > 1})
    if duplicate_new:
        errors.append(f"duplicate finding ids: {duplicate_new}")
    collisions = sorted(set(finding_ids) & set(historical))
    if collisions:
        errors.append(
            f"new findings reuse historical ids (reopen via prior_findings instead): {collisions}"
        )

    prior_ids = [p["id"] for p in raw["prior_findings"]]
    duplicate_prior = sorted({fid for fid in prior_ids if prior_ids.count(fid) > 1})
    if duplicate_prior:
        errors.append(f"duplicate prior-finding assessments: {duplicate_prior}")
    missing = sorted(set(historical) - set(prior_ids))
    if missing:
        errors.append(f"historical blocking findings not reconciled: {missing}")
    unknown = sorted(set(prior_ids) - set(historical))
    if unknown:
        errors.append(f"prior-finding assessments for unknown ids: {unknown}")
    for assessment in raw["prior_findings"]:
        fid = assessment["id"]
        if fid not in historical:
            continue
        state = historical[fid]
        outcome = PriorOutcome(assessment["outcome"])
        legal = LEGAL_PRIOR_OUTCOMES.get(state, frozenset())
        if outcome not in legal:
            errors.append(
                f"illegal lifecycle transition for {fid}: {state.value} -> {outcome.value}"
            )

    blocking_new = [f for f in raw["findings"] if f["severity"] in {s.value for s in BLOCKING_SEVERITIES}]
    unresolved_prior = [
        p
        for p in raw["prior_findings"]
        if p["outcome"]
        not in {PriorOutcome.VERIFIED_RESOLVED.value, PriorOutcome.REVIEWER_ACCEPTS_REJECTION.value}
    ]
    clean_expected = not blocking_new and not unresolved_prior
    if raw["verdict"] == "CLEAN" and not clean_expected:
        errors.append("verdict CLEAN with open blocking evidence")
    if raw["verdict"] == "FINDINGS" and clean_expected and raw["lens"] == Lens.GATING.value:
        errors.append("verdict FINDINGS without any blocking finding or unresolved prior")
    security = raw.get("security")
    if security is not None:
        # Every failed checklist item must be represented by its OWN blocking
        # finding, so the defect gets a stable ID and exact-set reconciliation
        # until resolved — it can never hitchhike on an unrelated finding.
        blocking_new_ids = {f["id"] for f in blocking_new}
        unresolved_prior_ids = {p["id"] for p in unresolved_prior}
        open_ids = blocking_new_ids | unresolved_prior_ids
        associated: dict[str, str] = {}
        for name in sorted(security):
            item = security[name]
            if item["result"] != "FAIL":
                continue
            finding_id = item.get("finding_id")
            if finding_id is None:
                errors.append(f"security item {name} FAIL without finding_id")
                continue
            if finding_id not in open_ids:
                errors.append(
                    f"security item {name} FAIL names {finding_id}, which is not an "
                    f"open blocking finding in this review"
                )
            if finding_id in associated:
                errors.append(
                    f"security items {associated[finding_id]} and {name} share "
                    f"finding {finding_id}; each FAIL needs its own finding"
                )
            associated[finding_id] = name
    if errors:
        raise ArtifactError("review", errors)

    new_findings = tuple(
        NewFinding(
            finding_id=f["id"],
            severity=Severity(f["severity"]),
            title=f["title"],
            requires_ruling=bool(f["requires_ruling"]),
            earlier_phase_gap=f["earlier_phase_gap"] is not None,
            unknown_contract=bool(f["unknown_contract"]),
        )
        for f in raw["findings"]
    )
    prior = tuple(
        PriorAssessment(finding_id=p["id"], outcome=PriorOutcome(p["outcome"]))
        for p in raw["prior_findings"]
    )
    p3 = tuple(
        (f["id"], f["title"]) for f in raw["findings"] if f["severity"] == Severity.P3.value
    )
    return ReviewSummary(
        lens=Lens(raw["lens"]),
        verdict=raw["verdict"],
        tree_digest=raw["tree_digest"],
        has_scope_observations=bool(raw["scope_observations"]),
        new_findings=new_findings,
        prior_findings=prior,
        p3_findings=p3,
    )


def validate_guidance(
    schemas: SchemaSet,
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    expected_finding_ids: frozenset[str],
    current_sha: str,
    current_tree: str,
    expected_review_kind: str,
    expected_reviewer_agent: str,
) -> GuidanceSummary:
    """Guidance is a review/v2 artifact with ``lens: guidance`` and no code."""
    check_persistence_safety("guidance", raw)
    _schema_validate(schemas, "review", raw)
    errors: list[str] = []
    _check_review_binding(raw, identity, current_sha, current_tree, errors)
    if raw["lens"] != Lens.GUIDANCE.value:
        errors.append(f"lens {raw['lens']!r} != expected 'guidance'")
    if raw["review_kind"] != expected_review_kind:
        errors.append(
            f"review_kind {raw['review_kind']!r} != lane's {expected_review_kind!r}"
        )
    if raw["reviewer"]["agent"] != expected_reviewer_agent:
        errors.append("reviewer agent is not the lane's authorized reviewer")
    if raw.get("handoff") is not None:
        errors.append("handoff provenance is not authorized for this lane")
    # Guidance is guidance-only: stop-bearing or review-only content (scope
    # observations, findings with routing flags, prior reconciliation, the
    # security checklist, open decisions, dependency declarations) must
    # arrive through a gating review, where the reducer/brief consumes it.
    # A guidance artifact carrying any of it is malformed — retry once, then
    # STOP — never accepted with that content discarded.
    if raw["scope_observations"]:
        errors.append("guidance artifact carries scope_observations (stop-bearing)")
    if raw["findings"]:
        errors.append("guidance artifact carries findings (not a guidance channel)")
    if raw["prior_findings"]:
        errors.append("guidance artifact carries prior_findings assessments")
    if raw.get("security") is not None:
        errors.append("guidance artifact carries a security checklist")
    if raw.get("open_decisions") is not None:
        errors.append("guidance artifact carries open_decisions")
    if raw.get("dependencies_added") is not None:
        errors.append("guidance artifact carries dependencies_added")
    guidance = raw.get("guidance")
    if guidance is None:
        errors.append("guidance block missing")
    else:
        provided = set(guidance["finding_ids"])
        if provided != expected_finding_ids:
            errors.append(
                f"guidance finding ids {sorted(provided)} != expected {sorted(expected_finding_ids)}"
            )
    if errors:
        raise ArtifactError("guidance", errors)
    return GuidanceSummary(finding_ids=tuple(sorted(guidance["finding_ids"])))


def validate_gate_result(
    schemas: SchemaSet,
    raw: Mapping[str, Any],
    identity: LaneIdentity,
    current_sha: str,
    current_tree: str,
) -> GateSummary:
    check_persistence_safety("target-gate-result", raw)
    _schema_validate(schemas, "target-gate-result", raw)
    errors: list[str] = []
    if raw["resolved_range"] != f"{identity.scope_base}..{current_sha}":
        errors.append("resolved_range does not match the verified lane range")
    if raw["tree_digest"] != current_tree:
        errors.append("tree_digest does not match the verified revision")
    if raw["manifest"] != identity.manifest:
        errors.append("manifest does not match the authorized lane")
    category = GateCategory(raw["category"])
    failed_checks = tuple(
        sorted(check["name"] for check in raw["checks"] if check["result"] == "FAIL")
    )
    if category in _ACCEPTING_GATE_CATEGORIES and failed_checks:
        errors.append(f"accepting category with FAIL checks: {list(failed_checks)}")
    if (
        category not in _ACCEPTING_GATE_CATEGORIES
        and category is not GateCategory.UNKNOWN
        and not failed_checks
    ):
        errors.append("failing category without any FAIL check")
    if errors:
        raise ArtifactError("target-gate-result", errors)
    return GateSummary(category=category, verdict=raw["verdict"], failed_checks=failed_checks)


# ---------------------------------------------------------------------------
# Summary -> typed event (shared by the coordinator and ledger replay, so a
# re-validated artifact provably derives the same event that was ledgered)
# ---------------------------------------------------------------------------


def event_from_author_summary(summary: AuthorResultSummary) -> AuthorResultAccepted:
    return AuthorResultAccepted(
        revision=summary.revision,
        commit=summary.commit,
        tree_digest=summary.tree_digest,
        has_unknown_contracts=summary.has_unknown_contracts,
    )


def event_from_fold_summary(summary: FoldSummary) -> FoldAccepted:
    return FoldAccepted(
        revision=summary.revision,
        commit=summary.commit,
        tree_digest=summary.tree_digest,
        dispositions=summary.dispositions,
        has_unknown_contracts=summary.has_unknown_contracts,
    )


def event_from_review_summary(summary: ReviewSummary) -> ReviewAccepted:
    return ReviewAccepted(
        lens=summary.lens,
        verdict=summary.verdict,
        has_scope_observations=summary.has_scope_observations,
        new_findings=summary.new_findings,
        prior_findings=summary.prior_findings,
    )


def event_from_guidance_summary(summary: GuidanceSummary) -> GuidanceAccepted:
    return GuidanceAccepted(finding_ids=summary.finding_ids)