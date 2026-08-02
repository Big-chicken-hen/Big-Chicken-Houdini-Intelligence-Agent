from __future__ import annotations

import unittest

from services.bridge.hia_bridge.project_payloads import (
    FailedClaim,
    NotApplicableClaim,
    UnverifiedClaim,
    VerifiedClaim,
    parse_review_claim,
    parse_stage_card,
)


def _step(step_id="STEP-1", requirement_id="REQ-1", dependencies=None):
    return {
        "step_id": step_id,
        "operation": "construct the requirement-specific editable subsystem",
        "dependencies": list(dependencies or []),
        "requirement_ids": [requirement_id],
        "inputs": [{"source": "authoritative task", "use": "dimensions"}],
        "outputs": [{"artifact": "native node subsystem"}],
        "acceptance": {"method": "inspect exact relationship"},
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
            "ordered_steps": [_step()],
            "evidence_contract": {"capture": "side and perspective"},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": "repair only the failed claim",
        }
        self.assertEqual("full", parse_stage_card(payload).depth)
        payload["reviewers"] = ["visual_review"]
        with self.assertRaisesRegex(ValueError, "both"):
            parse_stage_card(payload)

    def test_no_character_length_floor_exists(self) -> None:
        card = parse_stage_card(
            {
                "depth": "focused",
                "stage_id": "S",
                "requirement_ids": ["R"],
                "ordered_steps": [_step(requirement_id="R")],
            }
        )
        self.assertEqual("S", card.stage_id)

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
