"""Conditional structured payloads for plans and reviews.

These contracts validate shape, stable references, and the Full-blueprint
production information floors.  They do not score professionalism, keyword
overlap, or visual quality.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Iterable, Mapping


FULL_BLUEPRINT_INFORMATION_UNITS = 10_000
FULL_STAGE_INFORMATION_UNITS = 2_500
FULL_STEP_INFORMATION_UNITS = 350

# Stable, user-visible sections from the Full Goal blueprint reference.
FULL_BLUEPRINT_SECTION_IDS = (
    "goal_observable_completion",
    "user_facts_hard_constraints",
    "reference_observations",
    "codex_assumptions",
    "verified_scene_facts",
    "conflicts_risks_unverified_claims",
    "design_language_recognition_features",
    "subsystems_responsibilities",
    "editable_controls_parameter_dependencies",
    "outputs_delivery_contract",
    "stage_map",
    "complete_stage_acceptance_cards",
    "evidence_review_ledger",
    "revision_history",
)


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
    evidence_refs: tuple[str, ...]
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
    "not_applicable": {"disposition", "claim_id", "exemption"},
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
    exemption = value.get("exemption")
    if not isinstance(exemption, Mapping) or set(exemption) != {
        "reason",
        "evidence_refs",
    }:
        raise ValueError(
            "not_applicable exemption fields must be exactly reason and evidence_refs"
        )
    return NotApplicableClaim(
        claim_id,
        _required_text(exemption, "reason"),
        _optional_text_tuple(exemption, "evidence_refs"),
    )


@dataclass(frozen=True)
class StageCard:
    depth: str
    stage_id: str
    requirement_ids: tuple[str, ...]
    ordered_steps: tuple[Mapping[str, Any], ...]
    required_evidence: str | None = None
    failure_minimum_repair: str | None = None


def parse_stage_card(value: Mapping[str, Any]) -> StageCard:
    depth = value.get("depth")
    if depth not in {"direct", "focused", "full"}:
        raise ValueError("stage depth is invalid")
    common = {
        "depth",
        "stage_id",
        "requirement_ids",
        "ordered_steps",
        "required_evidence",
    }
    full = common | {"failure_minimum_repair"}
    expected = full if depth == "full" else common
    if set(value) != expected:
        raise ValueError(f"{depth} stage fields must be exactly {sorted(expected)}")
    requirement_ids = _text_tuple(value, "requirement_ids")
    raw_steps = value.get("ordered_steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("ordered_steps must be a non-empty list")
    steps = _validate_ordered_steps(raw_steps, set(requirement_ids))
    required_evidence = value.get("required_evidence")
    if required_evidence not in {"visual", "technical", "both"}:
        raise ValueError("required_evidence must be visual, technical, or both")
    if depth != "full":
        return StageCard(
            depth,
            _required_text(value, "stage_id"),
            requirement_ids,
            steps,
            required_evidence,
        )
    if information_units(value) < FULL_STAGE_INFORMATION_UNITS:
        raise ValueError("full stage has fewer than 2500 task-specific information units")
    for index, step in enumerate(steps, 1):
        if information_units(step) < FULL_STEP_INFORMATION_UNITS:
            raise ValueError(
                f"full ordered step {index} has fewer than 350 task-specific information units"
            )
    return StageCard(
        depth,
        _required_text(value, "stage_id"),
        requirement_ids,
        steps,
        required_evidence,
        _required_text(value, "failure_minimum_repair"),
    )


def validate_blueprint_information(
    value: Mapping[str, Any], cards: tuple[StageCard, ...]
) -> None:
    """Apply Full-only overall depth after every stage card is validated."""

    depths = {card.depth for card in cards}
    if len(depths) != 1:
        raise ValueError("all stage cards must use one task depth")
    if depths == {"full"} and information_units(value) < FULL_BLUEPRINT_INFORMATION_UNITS:
        raise ValueError(
            "Full blueprint has fewer than 10000 task-specific information units"
        )


def validate_plan_structure(
    value: Mapping[str, Any], *, allowed_source_anchors: Iterable[str]
) -> None:
    """Validate task facts and visible blueprint sections without judging prose."""

    expected = {
        "schema",
        "task_description",
        "user_facts",
        "blueprint_sections",
        "requirements",
        "stages",
    }
    if set(value) != expected:
        raise ValueError(f"plan fields must be exactly {sorted(expected)}")
    allowed = set(allowed_source_anchors)
    if not allowed:
        raise ValueError("authoritative source anchors are unavailable")
    description = value.get("task_description")
    if not isinstance(description, Mapping) or set(description) != {
        "description",
        "source_anchors",
    }:
        raise ValueError("task_description fields are invalid")
    _required_text(description, "description")
    _validate_source_anchors(description, allowed)

    raw_facts = value.get("user_facts")
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("user_facts must be a non-empty list")
    fact_ids: set[str] = set()
    for fact in raw_facts:
        if not isinstance(fact, Mapping) or set(fact) != {
            "fact_id",
            "description",
            "source_anchor",
        }:
            raise ValueError("user fact fields are invalid")
        fact_id = _required_text(fact, "fact_id")
        if fact_id in fact_ids:
            raise ValueError("user fact IDs must be unique")
        _required_text(fact, "description")
        if _required_text(fact, "source_anchor") not in allowed:
            raise ValueError("user fact source_anchor is not authoritative")
        fact_ids.add(fact_id)

    requirements = value.get("requirements")
    stages = value.get("stages")
    sections = value.get("blueprint_sections")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("requirements must be a non-empty list")
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages must be a non-empty list")
    if not isinstance(sections, list) or not sections:
        raise ValueError("blueprint_sections must be a non-empty list")
    requirement_ids = {
        _required_text(item, "requirement_id")
        for item in requirements
        if isinstance(item, Mapping)
    }
    stage_ids = {
        _required_text(item, "stage_id")
        for item in stages
        if isinstance(item, Mapping)
    }
    if len(requirement_ids) != len(requirements):
        raise ValueError("requirements are malformed or duplicated")
    if any(
        _required_text(item, "source_ref") not in allowed
        for item in requirements
        if isinstance(item, Mapping)
    ):
        raise ValueError("requirement source_ref is not authoritative")
    if len(stage_ids) != len(stages):
        raise ValueError("stages are malformed or duplicated")
    stage_depth_values = [
        item.get("depth")
        for item in stages
        if isinstance(item, Mapping)
    ]
    if (
        any(
            not isinstance(value, str)
            or value not in {"direct", "focused", "full"}
            for value in stage_depth_values
        )
        or len(set(stage_depth_values)) != 1
    ):
        raise ValueError("project-team blueprint stages must use one valid depth")
    full_depth = stage_depth_values[0] == "full"

    covered_requirement_facts: set[str] = set()
    requirement_facts: dict[str, set[str]] = {}
    for item in requirements:
        if not isinstance(item, Mapping) or set(item) != {
            "requirement_id",
            "kind",
            "description",
            "source_ref",
            "user_fact_ids",
        }:
            raise ValueError("requirement fields are invalid")
        _required_text(item, "kind")
        _required_text(item, "description")
        facts = set(_text_tuple(item, "user_fact_ids"))
        if not facts.issubset(fact_ids):
            raise ValueError("requirement references an unknown user fact")
        requirement_facts[str(item["requirement_id"])] = facts
        covered_requirement_facts.update(facts)
    if covered_requirement_facts != fact_ids:
        raise ValueError("requirements do not cover every user fact")

    observed_section_ids = tuple(
        _required_text(section, "section_id")
        for section in sections
        if isinstance(section, Mapping)
    )
    if full_depth and observed_section_ids != FULL_BLUEPRINT_SECTION_IDS:
        raise ValueError("blueprint sections must use all fourteen stable IDs in order")
    section_ids: set[str] = set()
    covered_facts: set[str] = set()
    covered_requirements: set[str] = set()
    covered_stages: set[str] = set()
    for section in sections:
        if not isinstance(section, Mapping) or set(section) != {
            "section_id",
            "title",
            "description",
            "source_anchors",
            "user_fact_ids",
            "requirement_ids",
            "stage_ids",
        }:
            raise ValueError("blueprint section fields are invalid")
        section_id = _required_text(section, "section_id")
        if section_id in section_ids:
            raise ValueError("blueprint section IDs must be unique")
        _required_text(section, "title")
        _required_text(section, "description")
        _validate_source_anchors(section, allowed)
        section_facts = set(_text_tuple(section, "user_fact_ids"))
        section_requirements = set(_text_tuple(section, "requirement_ids"))
        section_stages = set(_text_tuple(section, "stage_ids"))
        if not section_facts.issubset(fact_ids):
            raise ValueError("blueprint section references an unknown user fact")
        if not section_requirements.issubset(requirement_ids):
            raise ValueError("blueprint section references an unknown requirement")
        if not section_stages.issubset(stage_ids):
            raise ValueError("blueprint section references an unknown stage")
        section_ids.add(section_id)
        covered_facts.update(section_facts)
        covered_requirements.update(section_requirements)
        covered_stages.update(section_stages)
    if covered_facts != fact_ids:
        raise ValueError("blueprint sections do not cover every user fact")
    if covered_requirements != requirement_ids:
        raise ValueError("blueprint sections do not cover every requirement")
    if covered_stages != stage_ids:
        raise ValueError("blueprint sections do not cover every stage")

    covered_step_facts: set[str] = set()
    for stage in stages:
        for step in stage["ordered_steps"]:
            refs = set(_text_tuple(step, "user_fact_ids"))
            if not refs.issubset(fact_ids):
                raise ValueError("ordered step references an unknown user fact")
            required_facts = set().union(
                *(requirement_facts[item] for item in _text_tuple(step, "requirement_ids"))
            )
            if not required_facts.issubset(refs):
                raise ValueError("ordered step omits a fact required by its requirements")
            covered_step_facts.update(refs)
    if covered_step_facts != fact_ids:
        raise ValueError("ordered steps do not implement every user fact")


def _validate_source_anchors(value: Mapping[str, Any], allowed: set[str]) -> None:
    anchors = set(_text_tuple(value, "source_anchors"))
    if not anchors.issubset(allowed):
        raise ValueError("source_anchors contain a non-authoritative reference")


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


def _optional_text_tuple(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = value.get(key)
    if not isinstance(items, list) or not all(
        isinstance(item, str) and item.strip() for item in items
    ):
        raise ValueError(f"{key} must be a string list")
    return tuple(items)


_STEP_FIELDS = {
    "step_id",
    "dependencies",
    "requirement_ids",
    "user_fact_ids",
    "target_network_region",
    "native_operation_strategy",
    "connections",
    "parameter_dependencies",
    "expected_result",
    "evidence",
    "minimum_repair",
}


def _validate_ordered_steps(
    raw_steps: list[Any], stage_requirement_ids: set[str]
) -> tuple[Mapping[str, Any], ...]:
    seen_ids: set[str] = set()
    covered: set[str] = set()
    fingerprints: set[str] = set()
    parsed: list[Mapping[str, Any]] = []
    for raw in raw_steps:
        if not isinstance(raw, Mapping) or set(raw) != _STEP_FIELDS:
            raise ValueError(
                f"ordered step fields must be exactly {sorted(_STEP_FIELDS)}"
            )
        step_id = _required_text(raw, "step_id")
        if step_id in seen_ids:
            raise ValueError("ordered step IDs must be unique")
        dependencies = raw.get("dependencies")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            raise ValueError("step dependencies must be a string list")
        if any(item not in seen_ids for item in dependencies):
            raise ValueError("step dependencies must reference earlier ordered steps")
        requirement_ids = set(_text_tuple(raw, "requirement_ids"))
        _text_tuple(raw, "user_fact_ids")
        if not requirement_ids.issubset(stage_requirement_ids):
            raise ValueError("step references a requirement outside its stage")
        for key in (
            "target_network_region",
            "native_operation_strategy",
            "expected_result",
            "evidence",
            "minimum_repair",
        ):
            item = raw.get(key)
            if not isinstance(item, Mapping) or not item:
                raise ValueError(f"step {key} must be a structured record")
        for key in ("connections", "parameter_dependencies"):
            items = raw.get(key)
            if not isinstance(items, list) or not items or not all(
                isinstance(item, Mapping) and item for item in items
            ):
                raise ValueError(f"step {key} must contain structured records")
        fingerprint = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if fingerprint in fingerprints:
            raise ValueError("ordered steps must not repeat identical content")
        fingerprints.add(fingerprint)
        seen_ids.add(step_id)
        covered.update(requirement_ids)
        parsed.append(dict(raw))
    missing = sorted(stage_requirement_ids - covered)
    if missing:
        raise ValueError(f"ordered steps do not cover stage requirements: {missing}")
    return tuple(parsed)


def information_units(value: Any) -> int:
    """Count novel visible text while heavily discounting repeated filler.

    Field names never count.  Digits are normalized and repeated 12-character
    shingles count once across the complete value, so repeated tokens,
    duplicated paragraphs, and numbered copies cannot satisfy a floor.
    """

    texts: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not str(key).startswith("_"):
                    collect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)
        elif isinstance(item, str):
            texts.append(item)

    collect(value)
    seen: set[str] = set()
    units = 0
    for text in texts:
        normalized = _sanitize_information_text(text)
        if not normalized:
            continue
        width = min(12, len(normalized))
        shingles = {
            normalized[index : index + width]
            for index in range(len(normalized) - width + 1)
        }
        novel = shingles - seen
        seen.update(shingles)
        units += len(novel)
    return units


_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_VISIBLE_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9/._=:\\-]+")


def _sanitize_information_text(text: str) -> str:
    """Keep prose/technical names while removing machine-generated identity noise."""

    pieces: list[str] = []
    for match in _VISIBLE_TOKEN.finditer(text.casefold()):
        token = match.group(0)
        if "\u4e00" <= token[0] <= "\u9fff":
            pieces.append(token)
            continue
        if _UUID.fullmatch(token) or _suspicious_identifier(token):
            continue
        components = re.split(r"[/._=:\\-]+", token)
        kept = [
            component
            for component in components
            if component and not _suspicious_identifier(component)
        ]
        if kept:
            pieces.append("/".join(kept))
    return "".join(pieces)


def _suspicious_identifier(token: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", token.casefold())
    if not compact or compact.isdigit():
        return True
    if len(compact) >= 24 and all(character in "0123456789abcdef" for character in compact):
        return True
    # Paths remain useful because each component is checked separately below.
    if any(separator in token for separator in ("/", "\\", ":")):
        return False
    if len(compact) >= 48:
        return True
    if len(compact) < 24:
        return False
    frequencies = {character: compact.count(character) for character in set(compact)}
    entropy = -sum(
        (count / len(compact)) * math.log2(count / len(compact))
        for count in frequencies.values()
    )
    return entropy >= 3.7
