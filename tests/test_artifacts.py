"""Artifact validation: identity binding, exact-set reconciliation, safety."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from orchestrator.artifacts import (
    ArtifactError,
    DIAGNOSTIC_MAX_BYTES,
    bounded_diagnostic,
    check_persistence_safety,
    load_schema_set,
    validate_author_result,
    validate_fold,
    validate_gate_result,
    validate_guidance,
    validate_review,
)
from orchestrator.model import FindingState, GateCategory, Lens

from tests.fakes import (
    BASE_SHA,
    make_author_result,
    make_finding,
    make_fold,
    make_gate_result,
    make_guidance,
    make_identity,
    make_review,
    sha,
    tree,
)

SCHEMAS = load_schema_set(Path(__file__).resolve().parents[1] / "schemas" / "v2")
IDENTITY = make_identity()


def check_review(raw, historical=None, current_rev=1, lens=Lens.GATING):
    return validate_review(
        SCHEMAS,
        raw,
        identity=IDENTITY,
        expected_lens=lens,
        historical=historical or {},
        current_sha=sha(current_rev),
        current_tree=tree(current_rev),
    )


def check_gate(raw, current_rev=1):
    return validate_gate_result(
        SCHEMAS, raw, identity=IDENTITY, current_sha=sha(current_rev), current_tree=tree(current_rev)
    )


def check_fold(raw, outstanding, expected_revision):
    return validate_fold(
        SCHEMAS,
        raw,
        identity=IDENTITY,
        outstanding_ids=frozenset(outstanding),
        expected_revision=expected_revision,
        prev_sha=sha(expected_revision - 1),
    )


# -- author-result -----------------------------------------------------------


def test_author_result_accepts_and_rejects_revision_mismatch() -> None:
    summary = validate_author_result(
        SCHEMAS, make_author_result(), identity=IDENTITY, expected_revision=1
    )
    assert summary.revision == 1 and summary.commit == sha(1)
    with pytest.raises(ArtifactError, match="revision"):
        validate_author_result(
            SCHEMAS, make_author_result(revision=2), identity=IDENTITY, expected_revision=1
        )


def test_author_result_requires_full_shas() -> None:
    with pytest.raises(ArtifactError):
        validate_author_result(
            SCHEMAS, make_author_result(commit="abc1234"), identity=IDENTITY, expected_revision=1
        )


def test_author_result_bound_to_lane_identity() -> None:
    with pytest.raises(ArtifactError, match="work_item"):
        validate_author_result(
            SCHEMAS,
            make_author_result(work_item="another_phase"),
            identity=IDENTITY,
            expected_revision=1,
        )
    with pytest.raises(ArtifactError, match="scope_base"):
        validate_author_result(
            SCHEMAS,
            make_author_result(scope_base="c" * 40),
            identity=IDENTITY,
            expected_revision=1,
        )
    with pytest.raises(ArtifactError, match="no new revision"):
        validate_author_result(
            SCHEMAS,
            make_author_result(commit=BASE_SHA),
            identity=IDENTITY,
            expected_revision=1,
        )


def test_author_result_unknown_contracts_are_propagated_not_discarded() -> None:
    raw = make_author_result(
        unknown_contracts=[
            {
                "provider": "FMP",
                "endpoint": "/v4/thing",
                "question": "paging rule not established",
                "consulted": ["pinned docs"],
                "would_settle": "an audited capture",
            }
        ]
    )
    summary = validate_author_result(SCHEMAS, raw, identity=IDENTITY, expected_revision=1)
    assert summary.has_unknown_contracts is True


# -- fold: exact-set reconciliation and binding ------------------------------


def test_fold_exact_set_accepts_matching_dispositions() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED", "F2": "REJECTED_WITH_REASON"})
    summary = check_fold(fold, {"F1", "F2"}, expected_revision=2)
    assert {fid for fid, _ in summary.dispositions} == {"F1", "F2"}
    assert summary.tree_digest == tree(2)


def test_fold_missing_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    with pytest.raises(ArtifactError, match="missing dispositions.*F2"):
        check_fold(fold, {"F1", "F2"}, expected_revision=2)


def test_fold_unknown_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED", "GHOST": "FOLDED"})
    with pytest.raises(ArtifactError, match="unknown"):
        check_fold(fold, {"F1"}, expected_revision=2)


def test_fold_duplicate_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    fold["dispositions"] = fold["dispositions"] * 2
    with pytest.raises(ArtifactError, match="duplicate"):
        check_fold(fold, {"F1"}, expected_revision=2)


def test_fold_revision_mismatch_is_malformed() -> None:
    fold = make_fold(revision=3, dispositions={"F1": "FOLDED"})
    with pytest.raises(ArtifactError, match="revision"):
        check_fold(fold, {"F1"}, expected_revision=2)


def test_fold_bound_to_the_reviewed_range() -> None:
    fold = make_fold(
        revision=2,
        dispositions={"F1": "FOLDED"},
        folded_review_range=f"{'c' * 40}..{sha(1)}",
    )
    with pytest.raises(ArtifactError, match="folded_review_range"):
        check_fold(fold, {"F1"}, expected_revision=2)
    stale = make_fold(revision=2, dispositions={"F1": "FOLDED"}, commit=sha(1))
    with pytest.raises(ArtifactError, match="not a new revision"):
        check_fold(stale, {"F1"}, expected_revision=2)


# -- review: binding, reconciliation, lifecycle, verdict consistency ---------


def test_review_bound_to_the_verified_revision() -> None:
    with pytest.raises(ArtifactError, match="reviewed_range"):
        check_review(make_review(revision=2, verdict="CLEAN"), current_rev=1)
    wrong_tree = make_review(revision=1, verdict="CLEAN", tree_digest=tree(9))
    with pytest.raises(ArtifactError, match="tree_digest"):
        check_review(wrong_tree, current_rev=1)
    wrong_manifest = make_review(
        revision=1, verdict="CLEAN", manifest="dil-engine/manifests/other.yaml"
    )
    with pytest.raises(ArtifactError, match="manifest"):
        check_review(wrong_manifest, current_rev=1)


def test_review_must_reconcile_every_historical_blocker() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F3")], prior={})
    with pytest.raises(ArtifactError, match="not reconciled.*F1"):
        check_review(review, historical={"F1": FindingState.FIX_CLAIMED})


def test_review_unknown_prior_assessment_is_malformed() -> None:
    review = make_review(verdict="CLEAN", prior={"GHOST": "VERIFIED_RESOLVED"})
    with pytest.raises(ArtifactError, match="unknown ids"):
        check_review(review)


def test_review_illegal_lifecycle_transition_is_malformed() -> None:
    # A finding that was never resolved cannot be REOPENED.
    review = make_review(verdict="FINDINGS", prior={"F1": "REOPENED"})
    with pytest.raises(ArtifactError, match="illegal lifecycle"):
        check_review(review, historical={"F1": FindingState.FIX_CLAIMED})


def test_review_clean_with_unresolved_prior_is_rejected() -> None:
    review = make_review(verdict="CLEAN", prior={"F1": "STILL_PRESENT"})
    with pytest.raises(ArtifactError):
        check_review(review, historical={"F1": FindingState.FIX_CLAIMED})


def test_review_findings_verdict_without_blocking_evidence_is_rejected() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F9", "P3")], prior={})
    with pytest.raises(ArtifactError, match="without any blocking"):
        check_review(review)


def test_review_new_finding_reusing_a_historical_id_is_malformed() -> None:
    review = make_review(
        verdict="FINDINGS",
        findings=[make_finding("F1")],
        prior={"F1": "VERIFIED_RESOLVED"},
    )
    with pytest.raises(ArtifactError, match="reuse historical ids"):
        check_review(review, historical={"F1": FindingState.VERIFIED_RESOLVED})


def test_review_lens_mismatch_is_malformed() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F1")], lens="advisory")
    with pytest.raises(ArtifactError, match="lens"):
        check_review(review)


def test_gating_code_review_with_full_security_checklist_is_accepted() -> None:
    """End-to-end: the approved checklist field names pass persistence safety."""
    checks = {
        name: {"result": "PASS"}
        for name in (
            "parameterised_sql",
            "no_credential_logging",
            "least_privilege_db_objects",
            "job_robustness_contract",
            "provider_calls_bounded_documented",
            "dependencies_pinned_named",
            "no_dynamic_code_execution",
        )
    }
    review = make_review(verdict="CLEAN", review_kind="code", security=checks)
    summary = check_review(review)
    assert summary.verdict == "CLEAN"


def test_security_checklist_fail_requires_blocking_finding_evidence() -> None:
    checks = {
        name: {"result": "PASS"}
        for name in (
            "parameterised_sql",
            "no_credential_logging",
            "least_privilege_db_objects",
            "job_robustness_contract",
            "provider_calls_bounded_documented",
            "dependencies_pinned_named",
            "no_dynamic_code_execution",
        )
    }
    checks["parameterised_sql"] = {"result": "FAIL", "note": "string interpolation in query"}
    clean = make_review(verdict="CLEAN", review_kind="code", security=checks)
    with pytest.raises(ArtifactError, match="security checklist FAIL"):
        check_review(clean)
    represented = make_review(
        verdict="FINDINGS",
        review_kind="code",
        security=checks,
        findings=[make_finding("F1", "P1")],
    )
    assert check_review(represented).verdict == "FINDINGS"


# -- guidance ----------------------------------------------------------------


def test_guidance_exact_finding_set_is_required() -> None:
    guidance = make_guidance(["F1"], revision=1)
    summary = validate_guidance(
        SCHEMAS,
        guidance,
        identity=IDENTITY,
        expected_finding_ids=frozenset({"F1"}),
        current_sha=sha(1),
        current_tree=tree(1),
    )
    assert summary.finding_ids == ("F1",)
    with pytest.raises(ArtifactError, match="finding ids"):
        validate_guidance(
            SCHEMAS,
            guidance,
            identity=IDENTITY,
            expected_finding_ids=frozenset({"F1", "F2"}),
            current_sha=sha(1),
            current_tree=tree(1),
        )


def test_guidance_without_guidance_block_is_malformed() -> None:
    raw = make_guidance(["F1"], revision=1)
    del raw["guidance"]
    with pytest.raises(ArtifactError):
        validate_guidance(
            SCHEMAS,
            raw,
            identity=IDENTITY,
            expected_finding_ids=frozenset({"F1"}),
            current_sha=sha(1),
            current_tree=tree(1),
        )


# -- gate result -------------------------------------------------------------


def test_gate_result_returns_category_verdict_and_failed_checks() -> None:
    summary = check_gate(make_gate_result("PASS"))
    assert summary.category is GateCategory.PASS
    assert summary.verdict == "PASS" and summary.failed_checks == ()
    failing = check_gate(make_gate_result("FIXABLE_TEST"))
    assert failing.failed_checks == ("fixable_test",)


def test_gate_result_unknown_category_is_malformed() -> None:
    with pytest.raises(ArtifactError):
        check_gate(make_gate_result("LOOKS_FINE"))


def test_gate_result_bound_to_the_verified_revision() -> None:
    with pytest.raises(ArtifactError, match="resolved_range"):
        check_gate(make_gate_result("PASS", revision=2), current_rev=1)
    with pytest.raises(ArtifactError, match="tree_digest"):
        check_gate(make_gate_result("PASS", tree_digest=tree(9)))


def test_gate_passing_category_with_fail_check_is_contradictory() -> None:
    raw = make_gate_result("PASS")
    raw["checks"].append({"name": "tests", "result": "FAIL"})
    with pytest.raises(ArtifactError, match="accepting category with FAIL"):
        check_gate(raw)


def test_gate_failing_category_without_fail_check_is_contradictory() -> None:
    raw = make_gate_result("FIXABLE_TEST", checks=[{"name": "scope", "result": "PASS"}])
    with pytest.raises(ArtifactError, match="without any FAIL check"):
        check_gate(raw)


# -- persistence safety ------------------------------------------------------


def test_credential_named_field_is_rejected() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    fold["db_password"] = "x"
    with pytest.raises(ArtifactError, match="credential-named"):
        check_fold(fold, {"F1"}, expected_revision=2)


def test_connection_string_values_are_rejected() -> None:
    for value in (
        "postgresql://app:hunter2@db.internal/trading",
        "Driver={PostgreSQL};Server=db;Uid=app;Pwd=hunter2;",
        "Authorization uses Bearer abcdefghijKLMNOP1234",
    ):
        with pytest.raises(ArtifactError, match="credential-shaped"):
            check_persistence_safety("review", {"note": value})


def test_private_key_material_is_rejected() -> None:
    with pytest.raises(ArtifactError, match="key material"):
        check_persistence_safety("review", ["-----BEGIN RSA PRIVATE KEY-----"])


def test_token_usage_counts_and_author_fields_are_not_false_positives() -> None:
    review = make_review(
        verdict="CLEAN", reviewer={"agent": "codex", "tokens": {"input": 1, "output": 2}}
    )
    check_review(review)


def test_rejection_errors_are_sanitized_and_never_echo_values() -> None:
    review = make_review(verdict="CLEAN")
    review["verdict"] = "SECRETVALUE-hunter2"
    with pytest.raises(ArtifactError) as excinfo:
        check_review(review)
    assert "hunter2" not in str(excinfo.value)
    assert any(e.startswith("verdict:") for e in excinfo.value.errors)


def test_diagnostic_text_is_bounded_to_one_mebibyte() -> None:
    text = "x" * (2 * DIAGNOSTIC_MAX_BYTES)
    bounded = bounded_diagnostic(text)
    assert len(bounded.encode("utf-8")) <= DIAGNOSTIC_MAX_BYTES
    assert "[TRUNCATED reason=diagnostic-bound" in bounded
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() in bounded
    assert bounded_diagnostic("small") == "small"
