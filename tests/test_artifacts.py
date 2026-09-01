"""Artifact validation: exact-set reconciliation, lifecycle legality, safety."""

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

from tests.fakes import make_author_result, make_finding, make_fold, make_gate_result, make_guidance, make_review

SCHEMAS = load_schema_set(Path(__file__).resolve().parents[1] / "schemas" / "v2")


# -- author-result -----------------------------------------------------------


def test_author_result_accepts_and_rejects_revision_mismatch() -> None:
    summary = validate_author_result(SCHEMAS, make_author_result(), expected_revision=1)
    assert summary.revision == 1
    with pytest.raises(ArtifactError, match="revision"):
        validate_author_result(SCHEMAS, make_author_result(revision=2), expected_revision=1)


def test_author_result_requires_full_shas() -> None:
    with pytest.raises(ArtifactError):
        validate_author_result(
            SCHEMAS, make_author_result(commit="abc1234"), expected_revision=1
        )


# -- fold: exact-set reconciliation -----------------------------------------


def test_fold_exact_set_accepts_matching_dispositions() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED", "F2": "REJECTED_WITH_REASON"})
    summary = validate_fold(
        SCHEMAS, fold, outstanding_ids=frozenset({"F1", "F2"}), expected_revision=2
    )
    assert {fid for fid, _ in summary.dispositions} == {"F1", "F2"}


def test_fold_missing_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    with pytest.raises(ArtifactError, match="missing dispositions.*F2"):
        validate_fold(SCHEMAS, fold, outstanding_ids=frozenset({"F1", "F2"}), expected_revision=2)


def test_fold_unknown_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED", "GHOST": "FOLDED"})
    with pytest.raises(ArtifactError, match="unknown"):
        validate_fold(SCHEMAS, fold, outstanding_ids=frozenset({"F1"}), expected_revision=2)


def test_fold_duplicate_disposition_is_malformed() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    fold["dispositions"] = fold["dispositions"] * 2
    with pytest.raises(ArtifactError, match="duplicate"):
        validate_fold(SCHEMAS, fold, outstanding_ids=frozenset({"F1"}), expected_revision=2)


def test_fold_revision_mismatch_is_malformed() -> None:
    fold = make_fold(revision=3, dispositions={"F1": "FOLDED"})
    with pytest.raises(ArtifactError, match="revision"):
        validate_fold(SCHEMAS, fold, outstanding_ids=frozenset({"F1"}), expected_revision=2)


# -- review: reconciliation, lifecycle, verdict consistency ------------------


def test_review_must_reconcile_every_historical_blocker() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F3")], prior={})
    with pytest.raises(ArtifactError, match="not reconciled.*F1"):
        validate_review(
            SCHEMAS,
            review,
            expected_lens=Lens.GATING,
            historical={"F1": FindingState.FIX_CLAIMED},
        )


def test_review_unknown_prior_assessment_is_malformed() -> None:
    review = make_review(verdict="CLEAN", prior={"GHOST": "VERIFIED_RESOLVED"})
    with pytest.raises(ArtifactError, match="unknown ids"):
        validate_review(SCHEMAS, review, expected_lens=Lens.GATING, historical={})


def test_review_illegal_lifecycle_transition_is_malformed() -> None:
    # A finding that was never resolved cannot be REOPENED.
    review = make_review(verdict="FINDINGS", prior={"F1": "REOPENED"})
    with pytest.raises(ArtifactError, match="illegal lifecycle"):
        validate_review(
            SCHEMAS,
            review,
            expected_lens=Lens.GATING,
            historical={"F1": FindingState.FIX_CLAIMED},
        )


def test_review_clean_with_unresolved_prior_is_rejected() -> None:
    review = make_review(verdict="CLEAN", prior={"F1": "STILL_PRESENT"})
    with pytest.raises(ArtifactError):
        validate_review(
            SCHEMAS,
            review,
            expected_lens=Lens.GATING,
            historical={"F1": FindingState.FIX_CLAIMED},
        )


def test_review_findings_verdict_without_blocking_evidence_is_rejected() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F9", "P3")], prior={})
    with pytest.raises(ArtifactError, match="without any blocking"):
        validate_review(SCHEMAS, review, expected_lens=Lens.GATING, historical={})


def test_review_new_finding_reusing_a_historical_id_is_malformed() -> None:
    review = make_review(
        verdict="FINDINGS",
        findings=[make_finding("F1")],
        prior={"F1": "VERIFIED_RESOLVED"},
    )
    with pytest.raises(ArtifactError, match="reuse historical ids"):
        validate_review(
            SCHEMAS,
            review,
            expected_lens=Lens.GATING,
            historical={"F1": FindingState.VERIFIED_RESOLVED},
        )


def test_review_lens_mismatch_is_malformed() -> None:
    review = make_review(verdict="FINDINGS", findings=[make_finding("F1")], lens="advisory")
    with pytest.raises(ArtifactError, match="lens"):
        validate_review(SCHEMAS, review, expected_lens=Lens.GATING, historical={})


# -- guidance ----------------------------------------------------------------


def test_guidance_exact_finding_set_is_required() -> None:
    guidance = make_guidance(["F1"])
    summary = validate_guidance(SCHEMAS, guidance, expected_finding_ids=frozenset({"F1"}))
    assert summary.finding_ids == ("F1",)
    with pytest.raises(ArtifactError, match="finding ids"):
        validate_guidance(SCHEMAS, guidance, expected_finding_ids=frozenset({"F1", "F2"}))


def test_guidance_without_guidance_block_is_malformed() -> None:
    raw = make_guidance(["F1"])
    del raw["guidance"]
    with pytest.raises(ArtifactError):
        validate_guidance(SCHEMAS, raw, expected_finding_ids=frozenset({"F1"}))


# -- gate result -------------------------------------------------------------


def test_gate_result_returns_the_stable_category() -> None:
    assert (
        validate_gate_result(SCHEMAS, make_gate_result("PASS")) is GateCategory.PASS
    )


def test_gate_result_unknown_category_is_malformed() -> None:
    with pytest.raises(ArtifactError):
        validate_gate_result(SCHEMAS, make_gate_result("LOOKS_FINE"))


# -- persistence safety ------------------------------------------------------


def test_credential_named_field_is_rejected() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    fold["db_password"] = "x"
    with pytest.raises(ArtifactError, match="credential-named"):
        validate_fold(SCHEMAS, fold, outstanding_ids=frozenset({"F1"}), expected_revision=2)


def test_connection_string_value_is_rejected() -> None:
    with pytest.raises(ArtifactError, match="credential-shaped"):
        check_persistence_safety(
            "review", {"note": "postgresql://app:hunter2@db.internal/trading"}
        )


def test_private_key_material_is_rejected() -> None:
    with pytest.raises(ArtifactError, match="key material"):
        check_persistence_safety("review", ["-----BEGIN RSA PRIVATE KEY-----"])


def test_token_usage_counts_and_author_fields_are_not_false_positives() -> None:
    review = make_review(
        verdict="CLEAN", reviewer={"agent": "codex", "tokens": {"input": 1, "output": 2}}
    )
    validate_review(SCHEMAS, review, expected_lens=Lens.GATING, historical={})


def test_diagnostic_text_is_bounded_to_one_mebibyte() -> None:
    text = "x" * (2 * DIAGNOSTIC_MAX_BYTES)
    bounded = bounded_diagnostic(text)
    assert len(bounded.encode("utf-8")) <= DIAGNOSTIC_MAX_BYTES
    assert "[TRUNCATED reason=diagnostic-bound" in bounded
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() in bounded
    assert bounded_diagnostic("small") == "small"
