"""候选 Claim Schema 单元测试（无 DB）：语义红线与结构约束。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from src.claims.schemas import (
    BusinessStage,
    CandidateClaim,
    EvidenceState,
    ExtractorOutput,
    GroundedQuote,
)

COMPANY = uuid.uuid4()


def base_candidate(**overrides):
    payload = {
        "subject_entity_type": "Company",
        "subject_entity_id": COMPANY,
        "predicate_code": "PRODUCES",
        "object_value": {"stage": "RESEARCH"},
        "business_stage": BusinessStage.RESEARCH,
        "evidence_state": EvidenceState.PRODUCT_DISCLOSED,
        "confidence": 0.9,
        "quotes": [],
        "extraction_version": "rules-extractor-0.1.0",
        "ontology_version": "0.1.0",
    }
    payload.update(overrides)
    return CandidateClaim(**payload)


def test_stage_and_evidence_state_are_separate_enums():
    c = base_candidate()
    assert c.business_stage == BusinessStage.RESEARCH
    assert c.evidence_state == EvidenceState.PRODUCT_DISCLOSED
    with pytest.raises(ValidationError):
        base_candidate(business_stage="NOT_A_STAGE")
    with pytest.raises(ValidationError):
        base_candidate(evidence_state="PLATFORM_ONLY")


def test_negative_predicate_rejected():
    with pytest.raises(ValidationError, match="negation"):
        base_candidate(predicate_code="NOT_PRODUCES")
    with pytest.raises(ValidationError, match="negation"):
        base_candidate(predicate_code="does_not_supply")


def test_object_required():
    with pytest.raises(ValidationError, match="object"):
        base_candidate(object_value=None)


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        base_candidate(confidence=1.5)
    with pytest.raises(ValidationError):
        base_candidate(confidence=-0.1)


def test_quote_range_ordering():
    frag = uuid.uuid4()
    ver = uuid.uuid4()
    with pytest.raises(ValidationError):
        GroundedQuote(
            evidence_fragment_id=frag, document_version_id=ver,
            page_number=1, char_start=100, char_end=50, quote_text="x",
        )


def test_extractor_output_holds_rejected_notes_only():
    out = ExtractorOutput(
        claims=[], rejected_notes=["injection content treated as data: abc"],
        extraction_version="rules-extractor-0.1.0",
    )
    assert out.rejected_notes and not out.claims


def test_extraction_version_required():
    with pytest.raises(ValidationError):
        ExtractorOutput(claims=[], rejected_notes=[])
