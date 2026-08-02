"""Conditional structured payloads for plans and reviews.

These contracts validate shape and stable references only.  They do not score
professionalism, prose length, keyword overlap, or visual quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class VerifiedClaim:
    claim_id: str
    evidence_refs: tuple[str, ...]
    actual_evidence: str
    disposition: str = "verified"


@dataclass(frozen=True)
class FailedClaim:
    claim_id: str
    evidence_refs: tuple[str, ...]
    deviation: str
    minimum_repair: str
    disposition: str = "failed"


@dataclass(frozen=True)
class UnverifiedClaim:
    claim_id: str
    missing_evidence: tuple[str, ...]
    minimum_next_observation: str
    disposition: str = "unverified"


@dataclass(frozen=True)
class NotApplicableClaim:
    claim_id: str
    reason: str
    disposition: str = "not_applicable"


ReviewClaim = VerifiedClaim | FailedClaim | UnverifiedClaim | NotApplicableClaim


_CLAIM_FIELDS = {
    "verified": {"disposition", "claim_id", "evidence_refs", "actual_evidence"},
    "failed": {
        "disposition",
        "claim_id",
        "evidence_refs",
        "deviation",
        "minimum_repair",
    },
    "unverified": {
        "disposition",
        "claim_id",
        "missing_evidence",
        "minimum_next_observation",
    },
    "not_applicable": {"disposition", "claim_id", "reason"},
}


def parse_review_claim(value: Mapping[str, Any]) -> ReviewClaim:
    disposition = value.get("disposition")
    if disposition not in _CLAIM_FIELDS:
        raise ValueError("review disposition is invalid")
    expected = _CLAIM_FIELDS[disposition]
    if set(value) != expected:
        raise ValueError(
            f"{disposition} claim fields must be exactly {sorted(expected)}"
        )
    claim_id = _required_text(value, "claim_id")
    if disposition == "verified":
        return VerifiedClaim(
            claim_id,
            _text_tuple(value, "evidence_refs"),
            _required_text(value, "actual_evidence"),
        )
    if disposition == "failed":
        return FailedClaim(
            claim_id,
            _text_tuple(value, "evidence_refs"),
            _required_text(value, "deviation"),
            _required_text(value, "minimum_repair"),
        )
    if disposition == "unverified":
        return UnverifiedClaim(
            claim_id,
            _text_tuple(value, "missing_evidence"),
            _required_text(value, "minimum_next_observation"),
        )
    return NotApplicableClaim(claim_id, _required_text(value, "reason"))


@dataclass(frozen=True)
class StageCard:
    depth: str
    stage_id: str
    requirement_ids: tuple[str, ...]
    ordered_steps: tuple[Mapping[str, Any], ...]
    evidence_contract: Mapping[str, Any] | None = None
    reviewers: tuple[str, ...] = ()
    failure_minimum_repair: str | None = None


def parse_stage_card(value: Mapping[str, Any]) -> StageCard:
    depth = value.get("depth")
    if depth not in {"direct", "focused", "full"}:
        raise ValueError("stage depth is invalid")
    common = {"depth", "stage_id", "requirement_ids", "ordered_steps"}
    full = common | {"evidence_contract", "reviewers", "failure_minimum_repair"}
    expected = full if depth == "full" else common
    if set(value) != expected:
        raise ValueError(f"{depth} stage fields must be exactly {sorted(expected)}")
    requirement_ids = _text_tuple(value, "requirement_ids")
    raw_steps = value.get("ordered_steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("ordered_steps must be a non-empty list")
    if not all(isinstance(item, Mapping) and item for item in raw_steps):
        raise ValueError("every ordered step must be a non-empty object")
    if depth != "full":
        return StageCard(
            depth,
            _required_text(value, "stage_id"),
            requirement_ids,
            tuple(raw_steps),
        )
    evidence = value.get("evidence_contract")
    if not isinstance(evidence, Mapping) or not evidence:
        raise ValueError("full stage evidence_contract must be a non-empty object")
    reviewers = _text_tuple(value, "reviewers")
    if set(reviewers) != {"visual_review", "technical_review"}:
        raise ValueError("full stage requires both independent reviewers")
    return StageCard(
        depth,
        _required_text(value, "stage_id"),
        requirement_ids,
        tuple(raw_steps),
        evidence,
        reviewers,
        _required_text(value, "failure_minimum_repair"),
    )


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _text_tuple(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = value.get(key)
    if not isinstance(items, list) or not items or not all(
        isinstance(item, str) and item.strip() for item in items
    ):
        raise ValueError(f"{key} must be a non-empty string list")
    return tuple(items)
