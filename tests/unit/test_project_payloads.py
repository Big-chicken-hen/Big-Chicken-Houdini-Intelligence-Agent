from __future__ import annotations

import base64
import hashlib
import unittest
import uuid

from services.bridge.hia_bridge.project_payloads import (
    FULL_BLUEPRINT_SECTION_IDS,
    FailedClaim,
    NotApplicableClaim,
    UnverifiedClaim,
    VerifiedClaim,
    parse_review_claim,
    parse_stage_card,
    information_units,
    validate_blueprint_information,
    validate_plan_structure,
)


def _detail(label: str, size: int) -> str:
    actors = "builder reviewer designer operator artist rigger architect modeler engineer craftsperson supervisor specialist technician planner fabricator inspector author coordinator animator researcher developer".split()
    actions = "connects measures shapes aligns verifies documents constructs balances refines inspects positions configures preserves compares assembles routes exposes tests records evaluates repairs".split()
    targets = "roof frame support surface control output facade railing window doorway foundation stair canopy chassis cabin material camera light geometry network profile joint panel".split()
    reasons = "clearance editability silhouette contact evidence delivery proportion spacing continuity stability recognition dependency alignment accuracy hierarchy accessibility consistency readability integrity provenance".split()
    qualities = "precise modular coherent readable durable procedural reversible measurable visible native bounded layered proportional connected stable explicit organized responsive clean consistent".split()
    methods = "sweeping extruding beveling grouping routing sampling comparing capturing inspecting naming binding transforming merging filtering projecting validating".split()
    contexts = "object geometry material lighting animation simulation rendering viewport timeline hierarchy branch network interface output reference assembly".split()
    value = ""
    index = 0
    seed = sum(ord(character) for character in label)
    while len(value) < size:
        actor = actors[(seed + index * 5) % len(actors)]
        action = actions[(seed + index * 7) % len(actions)]
        target = targets[(seed + index * 11) % len(targets)]
        reason = reasons[(seed + index * 13) % len(reasons)]
        quality = qualities[(seed + index * 17) % len(qualities)]
        method = methods[(seed + index * 19) % len(methods)]
        context = contexts[(seed + index * 23) % len(contexts)]
        value += (
            f"{label} the {actor} {action} the editable {target} with native nodes "
            f"through a {quality} {method} method inside the {context} context so the "
            f"measured {reason} remains visible in the review evidence at "
            f"/obj/asset/{actor}_{action}_{target}_{reason}. "
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
    step["target_network_region"]["target"] = _detail("network target", 2500)
    step["native_operation_strategy"]["operation"] = _detail("native operation", 2500)
    step["connections"][0]["purpose"] = _detail("connection purpose", 2500)
    step["parameter_dependencies"][0]["effect"] = _detail("parameter effect", 2500)
    step["expected_result"]["visible"] = _detail("visible result", 2500)
    step["expected_result"]["editable"] = _detail("editable result", 2500)
    step["evidence"]["visual"] = _detail("visual evidence", 2500)
    step["evidence"]["technical"] = _detail("technical evidence", 2500)
    step["minimum_repair"]["trigger"] = _detail("repair trigger", 2500)
    step["minimum_repair"]["operation"] = _detail("repair operation", 2500)
    return step


def _plan_structure() -> dict:
    stage = {
        "depth": "full",
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
                "section_id": section_id,
                "title": section_id.replace("_", " "),
                "description": f"Task-specific content for {section_id}",
                "source_anchors": ["task:TASK-1"],
                "user_fact_ids": ["FACT-1"],
                "requirement_ids": ["REQ-1"],
                "stage_ids": ["STAGE-1"],
            }
            for section_id in FULL_BLUEPRINT_SECTION_IDS
        ],
        "requirements": [
            {
                "requirement_id": "REQ-1",
                "kind": "structure",
                "description": "The asset remains editable",
                "source_ref": "task:TASK-1",
                "user_fact_ids": ["FACT-1"],
            }
        ],
        "stages": [stage],
    }


class ProjectPayloadTests(unittest.TestCase):
    def test_information_units_keep_natural_language_and_houdini_paths(self) -> None:
        text = (
            "在 Houdini 中建立可编辑的屋顶结构并检查连接关系。 "
            "Build the native support network and verify measured clearance at "
            "/obj/HIA_RescueVehicle/OUT_FINAL using polyextrude height parameter."
        )
        self.assertGreater(information_units(text), 20)

    def test_hash_uuid_and_high_entropy_garbage_cannot_meet_full_floor(self) -> None:
        garbage_tokens = []
        for index in range(800):
            seed = f"semantic-floor-garbage-{index}".encode("utf-8")
            garbage_tokens.extend(
                (
                    hashlib.sha256(seed).hexdigest(),
                    str(uuid.uuid5(uuid.NAMESPACE_OID, seed.decode("utf-8"))),
                    base64.urlsafe_b64encode(hashlib.sha512(seed).digest()).decode("ascii"),
                )
            )
        garbage = " ".join(garbage_tokens)
        self.assertGreater(len(garbage), 10000)
        self.assertEqual(0, information_units(garbage))

        step = _step()
        step["target_network_region"]["target"] = garbage
        step["native_operation_strategy"]["operation"] = garbage
        step["connections"][0]["purpose"] = garbage
        step["parameter_dependencies"][0]["effect"] = garbage
        step["expected_result"] = {"visible": garbage, "editable": garbage}
        step["evidence"] = {"visual": garbage, "technical": garbage}
        step["minimum_repair"] = {"trigger": garbage, "operation": garbage}
        payload = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [step],
            "evidence_contract": {"capture": garbage, "technical": garbage},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": garbage,
        }
        with self.assertRaisesRegex(ValueError, "2500 task-specific"):
            parse_stage_card(payload)

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
                    "exemption": {
                        "reason": "task has no animation",
                        "evidence_refs": ["E4"],
                    },
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

    def test_not_applicable_requires_structured_reason_and_real_evidence_refs(self) -> None:
        for exemption in (
            {"reason": "", "evidence_refs": ["E1"]},
            {"reason": "outside this stage", "evidence_refs": []},
            {"reason": "outside this stage"},
        ):
            with self.subTest(exemption=exemption), self.assertRaises(ValueError):
                parse_review_claim(
                    {
                        "disposition": "not_applicable",
                        "claim_id": "C4",
                        "exemption": exemption,
                    }
                )

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

    def test_repeated_complete_paragraphs_do_not_inflate_overall_blueprint(self) -> None:
        stage = {
            "depth": "full",
            "stage_id": "S1",
            "requirement_ids": ["REQ-1"],
            "ordered_steps": [_full_step()],
            "evidence_contract": {"capture": "side and perspective"},
            "reviewers": ["visual_review", "technical_review"],
            "failure_minimum_repair": "repair only the failed claim",
        }
        card = parse_stage_card(stage)
        paragraph = _detail("onecopiedparagraph", 900)
        with self.assertRaisesRegex(ValueError, "10000 task-specific"):
            validate_blueprint_information(
                {"stages": [stage], "copied_sections": [paragraph] * 30},
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

    def test_plan_structure_rejects_missing_or_reordered_stable_section(self) -> None:
        payload = _plan_structure()
        payload["blueprint_sections"].pop(3)
        with self.assertRaisesRegex(ValueError, "fourteen stable IDs"):
            validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))

    def test_project_team_plan_cannot_downgrade_full_depth(self) -> None:
        payload = _plan_structure()
        payload["stages"][0]["depth"] = "focused"
        with self.assertRaisesRegex(ValueError, "must all use full depth"):
            validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))

    def test_every_ordered_step_reverse_references_a_user_fact(self) -> None:
        payload = _plan_structure()
        payload["stages"][0]["ordered_steps"][0]["user_fact_ids"] = ["UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "unknown user fact"):
            validate_plan_structure(payload, allowed_source_anchors=("task:TASK-1",))

    def test_requirements_and_steps_must_cover_every_authoritative_user_fact(self) -> None:
        payload = _plan_structure()
        payload["user_facts"].append(
            {
                "fact_id": "FACT-2",
                "description": "The user also requires native controls",
                "source_anchor": "task:TASK-1",
            }
        )
        for section in payload["blueprint_sections"]:
            section["user_fact_ids"].append("FACT-2")
        with self.assertRaisesRegex(ValueError, "requirements do not cover"):
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
