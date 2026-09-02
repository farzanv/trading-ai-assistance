"""The retained v1 draft schemas stay valid and reject the obvious malformed shapes.

v1 is draft test data only and cannot govern a lane (architecture §7.2); the
published V0 contracts are the v2 schemas covered by test_schemas_v2.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas" / "v1"


def _load(name: str) -> dict:
    with (SCHEMAS / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _clean_review() -> dict:
    return {
        "schema_version": 1,
        "reviewed_range": "5bfdc46..afd2a87",
        "tree_digest": "a" * 40,
        "manifest": "dil-engine/manifests/ca_platform_design_phase8.yaml",
        "lens": "design",
        "verdict": "CLEAN",
        "findings": [],
        "scope_observations": [],
        "reviewer": {"agent": "codex"},
    }


@pytest.mark.parametrize("name", ["review.schema.json", "fold.schema.json"])
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(_load(name))


def test_clean_review_validates() -> None:
    Draft202012Validator(_load("review.schema.json")).validate(_clean_review())


def test_review_rejects_unknown_verdict() -> None:
    review = _clean_review()
    review["verdict"] = "LOOKS_FINE"
    with pytest.raises(ValidationError):
        Draft202012Validator(_load("review.schema.json")).validate(review)


def test_review_rejects_extra_top_level_keys() -> None:
    review = _clean_review()
    review["free_text_summary"] = "prose must not be a channel"
    with pytest.raises(ValidationError):
        Draft202012Validator(_load("review.schema.json")).validate(review)


def test_review_finding_requires_required_change() -> None:
    review = _clean_review()
    review["verdict"] = "FINDINGS"
    review["findings"] = [{"id": "P9-R1-01", "severity": "P1", "title": "t", "description": "d"}]
    with pytest.raises(ValidationError):
        Draft202012Validator(_load("review.schema.json")).validate(review)


def test_fold_rejection_requires_reason() -> None:
    fold = {
        "schema_version": 1,
        "commit": "afd2a87",
        "revision": 5,
        "folded_review_range": "5bfdc46..afd2a87",
        "dispositions": [{"finding_id": "P9-R1-01", "disposition": "REJECTED_WITH_REASON"}],
        "author": {"agent": "claude"},
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(_load("fold.schema.json")).validate(fold)
    fold["dispositions"][0]["reason"] = "finding restates an approved Phase 7 contract"
    Draft202012Validator(_load("fold.schema.json")).validate(fold)
