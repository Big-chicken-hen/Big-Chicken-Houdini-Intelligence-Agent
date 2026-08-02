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
                "ordered_steps": [{"operation": "set parameter"}],
            }
        )
        self.assertEqual("direct", card.depth)
        self.assertIsNone(card.evidence_contract)

    def test_full_card_requires_both_reviews_and_evidence_contract(self) -> None:
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [{"operation": "build structure"}],
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
                "ordered_steps": [{"operation": "x"}],
            }
        )
        self.assertEqual("S", card.stage_id)


if __name__ == "__main__":
    unittest.main()
