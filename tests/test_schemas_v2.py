"""The four v2 contracts are valid JSON Schema and enforce the protocol rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.fakes import make_author_result, make_finding, make_fold, make_gate_result, make_guidance, make_review

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas" / "v2"

FILES = [
    "author-result.schema.json",
    "fold.schema.json",
    "review.schema.json",
    "target-gate-result.schema.json",
]


def _validator(name: str) -> Draft202012Validator:
    with (SCHEMAS / name).open(encoding="utf-8") as fh:
        return Draft202012Validator(json.load(fh))


@pytest.mark.parametrize("name", FILES)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    with (SCHEMAS / name).open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft202012Validator.check_schema(schema)
    assert "/v2/" in schema["$id"]
    assert schema["properties"]["schema_version"]["const"] == 2


def test_valid_samples_validate() -> None:
    _validator("author-result.schema.json").validate(make_author_result())
    _validator("fold.schema.json").validate(make_fold(revision=2, dispositions={"F1": "FOLDED"}))
    _validator("review.schema.json").validate(
        make_review(verdict="FINDINGS", findings=[make_finding("F1")])
    )
    _validator("review.schema.json").validate(make_guidance(["F1"]))
    _validator("target-gate-result.schema.json").validate(make_gate_result())


def test_advisory_or_guidance_lens_cannot_claim_clean() -> None:
    for lens in ("advisory", "guidance"):
        review = make_review(verdict="CLEAN", lens=lens)
        if lens == "guidance":
            review["guidance"] = make_guidance(["F1"])["guidance"]
        with pytest.raises(ValidationError):
            _validator("review.schema.json").validate(review)


def test_clean_with_blocking_finding_is_schema_invalid() -> None:
    review = make_review(verdict="CLEAN", findings=[make_finding("F1", "P1")])
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_clean_with_unresolved_prior_is_schema_invalid() -> None:
    review = make_review(verdict="CLEAN", prior={"F1": "STILL_PRESENT"})
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_blocking_finding_requires_root_cause_and_closure_evidence() -> None:
    finding = make_finding("F1", "P1")
    del finding["root_cause"]
    review = make_review(verdict="FINDINGS", findings=[finding])
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_guidance_lens_requires_the_guidance_block() -> None:
    review = make_review(verdict="FINDINGS", lens="guidance")
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_gating_code_review_requires_the_security_checklist() -> None:
    review = make_review(
        verdict="FINDINGS", findings=[make_finding("F1")], review_kind="code"
    )
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_prior_findings_are_required() -> None:
    review = make_review(verdict="CLEAN")
    del review["prior_findings"]
    with pytest.raises(ValidationError):
        _validator("review.schema.json").validate(review)


def test_fold_folded_disposition_requires_root_cause_and_files() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"})
    del fold["dispositions"][0]["root_cause"]
    with pytest.raises(ValidationError):
        _validator("fold.schema.json").validate(fold)


def test_fold_rejection_requires_a_reason() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "REJECTED_WITH_REASON"})
    del fold["dispositions"][0]["reason"]
    with pytest.raises(ValidationError):
        _validator("fold.schema.json").validate(fold)


def test_fold_rejects_short_shas() -> None:
    fold = make_fold(revision=2, dispositions={"F1": "FOLDED"}, commit="abc1234")
    with pytest.raises(ValidationError):
        _validator("fold.schema.json").validate(fold)


def test_gate_result_category_enum_is_closed() -> None:
    with pytest.raises(ValidationError):
        _validator("target-gate-result.schema.json").validate(make_gate_result("LOOKS_FINE"))


def test_gate_result_requires_verdict_and_tree_digest() -> None:
    for field in ("verdict", "tree_digest"):
        raw = make_gate_result("PASS")
        del raw[field]
        with pytest.raises(ValidationError):
            _validator("target-gate-result.schema.json").validate(raw)


def test_gate_verdict_must_be_consistent_with_the_category() -> None:
    contradiction = make_gate_result("PASS", verdict="FAIL")
    with pytest.raises(ValidationError):
        _validator("target-gate-result.schema.json").validate(contradiction)
    contradiction = make_gate_result("SCOPE", verdict="PASS")
    with pytest.raises(ValidationError):
        _validator("target-gate-result.schema.json").validate(contradiction)


def test_finding_routing_flags_are_required_not_defaulted() -> None:
    for field in ("requires_ruling", "earlier_phase_gap", "blocks_downstream", "unknown_contract", "section"):
        finding = make_finding("F1")
        del finding[field]
        review = make_review(verdict="FINDINGS", findings=[finding])
        with pytest.raises(ValidationError):
            _validator("review.schema.json").validate(review)


def test_gate_result_requires_at_least_one_check() -> None:
    with pytest.raises(ValidationError):
        _validator("target-gate-result.schema.json").validate(make_gate_result(checks=[]))


@pytest.mark.parametrize("name", FILES)
def test_extra_top_level_keys_are_rejected(name: str) -> None:
    samples = {
        "author-result.schema.json": make_author_result(),
        "fold.schema.json": make_fold(revision=2, dispositions={"F1": "FOLDED"}),
        "review.schema.json": make_review(verdict="CLEAN"),
        "target-gate-result.schema.json": make_gate_result(),
    }
    sample = samples[name]
    sample["free_text_summary"] = "prose must not be a channel"
    with pytest.raises(ValidationError):
        _validator(name).validate(sample)
