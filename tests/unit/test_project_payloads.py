from __future__ import annotations

import hashlib
import unittest

from services.bridge.hia_bridge.project_payloads import (
    FailedClaim,
    NotApplicableClaim,
    UnverifiedClaim,
    VerifiedClaim,
    parse_review_claim,
    parse_stage_card,
    validate_blueprint_information,
    validate_plan_structure,
)


def _detail(label: str, size: int) -> str:
    value = label
    index = 0
    translation = str.maketrans("0123456789", "abcdefghij")
    while len(value) < size:
        value += hashlib.sha256(f"{label}:{index}".encode()).hexdigest().translate(
            translation
        )
        index += 1
    return value[:size]


def _step(step_id="STEP-1", requirement_id="REQ-1", dependencies=None):
    return {
        "step_id": step_id,
        "dependencies": list(dependencies or []),
        "requirement_ids": [requirement_id],
        "user_fact_ids": ["FACT-1"],
        "target_network_region": {"context": "SOP", "target": "asset body"},
        "native_operation_strategy": {
            "native_nodes": ["polyextrude"],
            "operation": "construct the requirement-specific editable subsystem",
        },
        "connections": [
            {"from": "source/0", "to": "shape/0", "purpose": "geometry flow"}
        ],
        "parameter_dependencies": [
            {"parameter": "shape/height", "depends_on": "FACT-1", "effect": "sets height"}
        ],
        "expected_result": {"visible": "recognizable form", "editable": "native controls"},
        "evidence": {"visual": "perspective capture", "technical": "geometry summary"},
        "minimum_repair": {"trigger": "shape mismatch", "operation": "adjust height only"},
    }


def _full_step():
    step = _step()
    step["native_operation_strategy"]["operation"] = _detail("operation", 700)
    step["expected_result"]["visible"] = _detail("visible", 700)
    step["evidence"]["technical"] = _detail("technical", 700)
    step["minimum_repair"]["operation"] = _detail("repair", 700)
    return step


def _plan_structure() -> dict:
    stage = {
        "depth": "focused",
        "stage_id": "STAGE-1",
        "requirement_ids": ["REQ-1"],
        "ordered_steps": [_step()],
    }
    return {
        "schema": "hia-project-plan/1",
        "task_description": {
            "description": "Build the editable asset described by the user",
            "source_anchors": ["task:TASK-1"],
        },
        "user_facts": [
            {
                "fact_id": "FACT-1",
                "description": "The user requires an editable asset",
                "source_anchor": "task:TASK-1",
            }
        ],
        "blueprint_sections": [
            {
                "section_id": "SECTION-1",
                "title": "Editable construction",
                "description": "Construct and verify the editable native network",
                "source_anchors": ["task:TASK-1"],
                "user_fact_ids": ["FACT-1"],
                "requirement_ids": ["REQ-1"],
                "stage_ids": ["STAGE-1"],
            }
        ],
        "requirements": [
            {"requirement_id": "REQ-1", "kind": "structure", "source_ref": "task:TASK-1"}
        ],
        "stages": [stage],
    }


class ProjectPayloadTests(unittest.TestCase):
    def test_review_claim_dispositions_have_only_applicable_fields(self) -> None:
        values = (
            (
                {
                    "disposition": "verified",
                    "claim_id": "C1",
                    "evidence_refs": ["E1"],
                    "actual_evidence": "measured contact is zero",
                },
                VerifiedClaim,
            ),
            (
                {
                    "disposition": "failed",
                    "claim_id": "C2",
                    "evidence_refs": ["E2"],
                    "deviation": "intersects",
                    "minimum_repair": "move support clear",
                },
                FailedClaim,
            ),
            (
                {
                    "disposition": "unverified",
                    "claim_id": "C3",
                    "missing_evidence": ["side capture"],
                    "minimum_next_observation": "capture side view",
                },
                UnverifiedClaim,
            ),
            (
                {
                    "disposition": "not_applicable",
                    "claim_id": "C4",
                    "reason": "task has no animation",
                },
                NotApplicableClaim,
            ),
        )
        for payload, expected in values:
            with self.subTest(disposition=payload["disposition"]):
                self.assertIsInstance(parse_review_claim(payload), expected)

    def test_mutually_exclusive_review_fields_are_rejected(self) -> None:
        payload = {
            "disposition": "verified",
            "claim_id": "C1",
            "evidence_refs": ["E1"],
            "actual_evidence": "ok",
            "minimum_repair": "must not be accepted",
        }
        with self.assertRaisesRegex(ValueError, "exactly"):
            parse_review_claim(payload)

    def test_direct_card_is_small_but_structured(self) -> None:
        card = parse_stage_card(
            {
                "depth": "direct",
                "stage_id": "S1",
                "requirement_ids": ["REQ-1"],
                "ordered_steps": [_step()],
            }
        )
        self.assertEqual("direct", card.depth)
        self.assertIsNone(card.evidence_contract)

    def test_full_card_requires_both_reviews_and_evidence_contract(self) -> None:
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [_full_step()],
            "evidence_contract": {"capture": "side and perspective"},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": "repair only the failed claim",
        }
        self.assertEqual("full", parse_stage_card(payload).depth)
        payload["reviewers"] = ["visual_review"]
        with self.assertRaisesRegex(ValueError, "both"):
            parse_stage_card(payload)

    def test_focused_card_has_no_full_information_floor(self) -> None:
        card = parse_stage_card(
            {
                "depth": "focused",
                "stage_id": "S",
                "requirement_ids": ["R"],
                "ordered_steps": [_step(requirement_id="R")],
            }
        )
        self.assertEqual("S", card.stage_id)

    def test_full_card_rejects_shallow_stage_and_step(self) -> None:
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [_step()],
            "evidence_contract": {"capture": "side and perspective"},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": "repair only the failed claim",
        }
        with self.assertRaisesRegex(ValueError, "2500 task-specific"):
            parse_stage_card(payload)

    def test_full_blueprint_rejects_stage_detail_below_overall_floor(self) -> None:
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [_full_step()],
            "evidence_contract": {"capture": "side and perspective"},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": "repair only the failed claim",
        }
        card = parse_stage_card(payload)
        with self.assertRaisesRegex(ValueError, "10000 task-specific"):
            validate_blueprint_information(
                {"schema": "hia-project-plan/1", "stages": [payload]},
                (card,),
            )

    def test_repeated_tokens_and_numbered_paragraphs_do_not_satisfy_full_floor(self) -> None:
        step = _step()
        repeated = ("same generic detail " * 2000) + " ".join(
            f"same generic paragraph {index}" for index in range(2000)
        )
        step["native_operation_strategy"]["operation"] = repeated
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [step],
            "evidence_contract": {"capture": repeated},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": repeated,
        }
        with self.assertRaisesRegex(ValueError, "2500 task-specific"):
            parse_stage_card(payload)

    def test_plan_structure_requires_authoritative_anchors_and_complete_sections(self) -> None:
        payload = _plan_structure()
        validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))
        payload["blueprint_sections"][0]["user_fact_ids"] = ["UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "unknown user fact"):
            validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))

    def test_every_ordered_step_reverse_references_a_user_fact(self) -> None:
        payload = _plan_structure()
        payload["stages"][0]["ordered_steps"][0]["user_fact_ids"] = ["UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "unknown user fact"):
            validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))

    def test_stage_steps_require_semantic_structure_and_exact_coverage(self) -> None:
        base = {
            "depth": "focused",
            "stage_id": "S",
            "requirement_ids": ["R1", "R2"],
            "ordered_steps": [_step(requirement_id="R1")],
        }
        with self.assertRaisesRegex(ValueError, "do not cover"):
            parse_stage_card(base)
        base["ordered_steps"].append(
            _step("STEP-2", "R2", dependencies=["STEP-1"])
        )
        card = parse_stage_card(base)
        self.assertEqual(2, len(card.ordered_steps))


if __name__ == "__main__":
    unittest.main()
