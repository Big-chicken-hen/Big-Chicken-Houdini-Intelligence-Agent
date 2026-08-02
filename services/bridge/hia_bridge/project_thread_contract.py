"""Fixed role metadata and structured handoffs for Panel project Threads."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .errors import BridgeError


PROJECT_ROLE_ORDER = (
    "supervisor",
    "planning",
    "execution",
    "visual_review",
    "technical_review",
)
PROJECT_READ_ONLY_ROLES = frozenset(
    {"supervisor", "planning", "visual_review", "technical_review"}
)
PROJECT_HIA_DISABLE_CONFIG = {
    "mcp_servers.hia_mcp_v2.enabled": False,
    "mcp_servers.houdini_intelligence.enabled": False,
}
PROJECT_HIA_ENABLE_CONFIG = {
    "mcp_servers.hia_mcp_v2.enabled": True,
    "mcp_servers.houdini_intelligence.enabled": True,
}
PROJECT_TEAM_READ_ONLY_COLLAB_CONFIG = {"multi_agent_mode": "proactive"}
PROJECT_TEAM_EXECUTION_COLLAB_CONFIG = {
    "multi_agent_mode": "explicitRequestOnly"
}


def project_role_config(*, read_only: bool) -> dict[str, Any]:
    return {
        **(PROJECT_HIA_DISABLE_CONFIG if read_only else PROJECT_HIA_ENABLE_CONFIG),
        **(
            PROJECT_TEAM_READ_ONLY_COLLAB_CONFIG
            if read_only
            else PROJECT_TEAM_EXECUTION_COLLAB_CONFIG
        ),
    }
ROLE_ALIASES = {
    "planner": "planning",
    "build_blueprint": "planning",
    "executor": "execution",
}
ROLE_TITLES = {
    "supervisor": "监督 AI",
    "planning": "方案 AI",
    "execution": "执行 AI",
    "visual_review": "视觉审查 AI",
    "technical_review": "技术审查 AI",
}
ROLE_RESPONSIBILITIES = {
    "supervisor": "守住用户约束，授权完整阶段卡，并依据真实证据签发最小修复卡。",
    "planning": "维护完整蓝图，产出可执行、可验证且含完整证据契约的顺序阶段卡。",
    "execution": "唯一写入当前 HIP 的角色；一次只执行一张阶段卡。",
    "visual_review": "只读检查真实图像中的目标、比例、构图、材质与参考一致性。",
    "technical_review": "只读检查结构、可编辑性、连接、依赖、性能与技术证据。",
}

_TEXT_ARRAY = {"type": "array", "items": {"type": "string"}}
_COLLABORATION_MODE = {
    "type": "string",
    "enum": ["used-with-real-events", "serial-fallback"],
}
_TASK_DEPTH = {
    "type": "string",
    "enum": ["direct", "focused", "full"],
}
_SCENE_TASK_ELIGIBILITY = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reason"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["eligible", "not_applicable", "unclear"],
        },
        "reason": {"type": "string"},
    },
}
_PROJECT_FACT_FIELDS = (
    "user_facts",
    "reference_observations",
    "codex_assumptions",
    "verified_scene_facts",
    "hard_constraints",
    "acceptance",
)
_ORDERED_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "responsibility",
        "path_or_network_region",
        "native_operation_family",
        "inputs_and_connections",
        "key_parameters",
        "expected_result",
        "next_step_evidence",
        "failure_minimum_repair",
    ],
    "properties": {
        "responsibility": {"type": "string"},
        "path_or_network_region": {"type": "string"},
        "native_operation_family": {"type": "string"},
        "inputs_and_connections": {"type": "string"},
        "key_parameters": {"type": "string"},
        "expected_result": {"type": "string"},
        "next_step_evidence": {"type": "string"},
        "failure_minimum_repair": {"type": "string"},
    },
}
_EVIDENCE_DISPOSITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["technical", "visual", "stage"],
    "properties": {
        "technical": {
            "type": "string",
            "enum": ["pending", "verified", "unverified", "failed"],
        },
        "visual": {
            "type": "string",
            "enum": ["pending", "verified", "unverified", "failed"],
        },
        "stage": {
            "type": "string",
            "enum": [
                "not started",
                "active",
                "needs repair",
                "passed",
                "blocked",
                "not applicable",
            ],
        },
    },
}
_STAGE_FIELDS = (
    "title",
    "objective",
    "prerequisites",
    "inputs",
    "ordered_construction_steps",
    "native_node_strategy",
    "authoring_batches",
    "parameter_dependencies",
    "outputs",
    "visible_characteristics",
    "structural_relationships",
    "prohibitions",
    "technical_evidence",
    "visual_evidence",
    "reviewers",
    "failure_minimum_repair",
    "downstream_contract",
    "evidence_disposition",
)
STAGE_CARD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_STAGE_FIELDS),
    "properties": {
        "title": {"type": "string"},
        "objective": {"type": "string"},
        "prerequisites": _TEXT_ARRAY,
        "inputs": _TEXT_ARRAY,
        "ordered_construction_steps": {
            "type": "array",
            "minItems": 1,
            "items": _ORDERED_STEP_SCHEMA,
        },
        "native_node_strategy": _TEXT_ARRAY,
        "authoring_batches": _TEXT_ARRAY,
        "parameter_dependencies": _TEXT_ARRAY,
        "outputs": _TEXT_ARRAY,
        "visible_characteristics": _TEXT_ARRAY,
        "structural_relationships": _TEXT_ARRAY,
        "prohibitions": _TEXT_ARRAY,
        "technical_evidence": _TEXT_ARRAY,
        "visual_evidence": _TEXT_ARRAY,
        "reviewers": _TEXT_ARRAY,
        "failure_minimum_repair": _TEXT_ARRAY,
        "downstream_contract": _TEXT_ARRAY,
        "evidence_disposition": _EVIDENCE_DISPOSITION_SCHEMA,
    },
}
_PLAN_PROPERTIES = {
    "project_title": {"type": "string"},
    "summary": {"type": "string"},
    "task_depth": _TASK_DEPTH,
    "collaboration_mode": _COLLABORATION_MODE,
    **{field: _TEXT_ARRAY for field in _PROJECT_FACT_FIELDS},
    "stages": {
        "type": "array",
        "minItems": 1,
        "items": STAGE_CARD_SCHEMA,
    },
}

SUPERVISOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "scene_task_eligibility",
        "summary",
        "task_depth",
        "collaboration_mode",
        *_PROJECT_FACT_FIELDS,
    ],
    "properties": {
        "scene_task_eligibility": _SCENE_TASK_ELIGIBILITY,
        "summary": {"type": "string"},
        "task_depth": _TASK_DEPTH,
        "collaboration_mode": _COLLABORATION_MODE,
        **{field: _TEXT_ARRAY for field in _PROJECT_FACT_FIELDS},
    },
}
PLANNING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "project_title",
        "summary",
        "task_depth",
        "collaboration_mode",
        *_PROJECT_FACT_FIELDS,
        "stages",
    ],
    "properties": _PLAN_PROPERTIES,
}
EXECUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "summary",
        "technical_evidence",
        "remaining",
        "review_images",
    ],
    "properties": {
        "outcome": {"type": "string", "enum": ["completed", "blocked"]},
        "summary": {"type": "string"},
        "technical_evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "tool_item_id",
                    "claim",
                    "scope_or_path",
                    "frame_or_time",
                    "observation_or_measurement",
                    "source",
                    "result",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "tool_item_id": {"type": "string"},
                    "claim": {"type": "string"},
                    "scope_or_path": {"type": "string"},
                    "frame_or_time": {"type": "string"},
                    "observation_or_measurement": {"type": "string"},
                    "source": {"type": "string"},
                    "result": {"type": "string"},
                },
            },
        },
        "remaining": {"type": "array", "items": {"type": "string"}},
        "review_images": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "capture_tool_item_id",
                    "path",
                    "frame_or_time",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "capture_tool_item_id": {"type": "string"},
                    "path": {"type": "string"},
                    "frame_or_time": {"type": "string"},
                },
            },
        },
        "collaboration_mode": _COLLABORATION_MODE,
    },
}
EXECUTION_SCHEMA["required"].append("collaboration_mode")
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "claims",
        "largest_consequential_deviation",
        "missing_evidence",
        "repair",
    ],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["pass", "repair", "blocked"],
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "stage_claim",
                    "disposition",
                    "evidence_refs",
                    "actual_evidence",
                    "deviation",
                    "minimum_repair",
                    "not_applicable_reason",
                ],
                "properties": {
                    "stage_claim": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": [
                            "verified",
                            "unverified",
                            "failed",
                            "not_applicable",
                        ],
                    },
                    "actual_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "deviation": {"type": "string"},
                    "minimum_repair": {"type": "string"},
                    "not_applicable_reason": {"type": "string"},
                },
            },
        },
        "largest_consequential_deviation": {"type": "string"},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "repair": {"type": "array", "items": {"type": "string"}},
        "collaboration_mode": _COLLABORATION_MODE,
    },
}
REVIEW_SCHEMA["required"].append("collaboration_mode")
_EXTERNAL_DEPENDENCY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "proven",
        "required_external_change",
        "observed_blocker",
        "why_codex_cannot_resolve",
        "evidence_refs",
    ],
    "properties": {
        "proven": {"type": "boolean"},
        "required_external_change": {"type": "string"},
        "observed_blocker": {"type": "string"},
        "why_codex_cannot_resolve": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
}
SUPERVISOR_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "summary", "repair_card", "collaboration_mode"],
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "repair", "blocked"]},
        "summary": {"type": "string"},
        "repair_card": {"type": "string"},
        "collaboration_mode": _COLLABORATION_MODE,
        "external_dependency": _EXTERNAL_DEPENDENCY_SCHEMA,
    },
}

SUPERVISOR_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(PLANNING_SCHEMA["required"]),
    "properties": _PLAN_PROPERTIES,
}

SUPERVISOR_BLOCKED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "summary", "repair_card", "collaboration_mode"],
    "properties": {
        "decision": {"type": "string", "enum": ["repair", "blocked"]},
        "summary": {"type": "string"},
        "repair_card": {"type": "string"},
        "collaboration_mode": _COLLABORATION_MODE,
        "external_dependency": _EXTERNAL_DEPENDENCY_SCHEMA,
    },
}


def role_instructions(role: str) -> str:
    """Return the role boundary installed on its app-server Thread."""

    role = ROLE_ALIASES.get(role, role)
    common = (
        "You are one Codex app-server Thread in an HIA Panel project. "
        "Follow the repository task-depth blueprint contract for provenance, complete "
        "current-stage cards, bounded authoring, and evidence. Never let a Codex "
        "assumption, reference observation, or reviewer preference weaken a User fact. "
        "Do not create extra projects, duplicate project roles, sidebar tasks, "
        "planners, gates, memory, or schedulers. Never claim subagent use without "
        "matching native collaboration events. "
        "The Bridge coordinator exclusively owns the native Goal lifecycle; never "
        "call create_goal, get_goal, or update_goal, and never change Goal status. "
        "Return only the JSON required by turn/start outputSchema."
    )
    if role == "execution":
        return (
            common
            + " You are the sole scene writer. Work on exactly the supplied current "
            "stage or repair card, validate actual scene evidence, and never delegate "
            "any HIA, HOM, HIP, repository, or filesystem operation. Do not proactively "
            "spawn internal subagents because the app-server cannot independently "
            "prove a child has a read-only HIA-disabled profile. All writes and their "
            "evidence acquisition stay serial on this role mainline."
        )
    return (
        common
        + " When native collaboration tools are actually available, dispatch suitable "
        "independent read-only research or review inside this role and let real native "
        "events show that work. Internal subagents remain read-only additional capacity; "
        "when tools are unavailable, perform the same-quality work serially."
        + " You are strictly read-only. Never modify the HIP, repository, filesystem, "
        "or external state, and never call HIA MCP, Houdini MCP, HOM, or hython."
    )


def handoff(value: Any, *, max_bytes: int = 1_048_576) -> str:
    """Encode a bounded deterministic handoff without interpreting its content."""

    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(text.encode("utf-8")) > max_bytes:
        raise BridgeError(
            "PROJECT_THREAD_PAYLOAD_TOO_LARGE",
            "A project Thread handoff exceeds its bounded size",
            http_status=502,
        )
    return text


def _constraint_capsule(supervisor: Mapping[str, Any]) -> str:
    """Carry authoritative user intent through every bounded stage loop."""

    return handoff(
        {
            "project_title": supervisor.get("project_title"),
            "goal_summary": supervisor.get("summary"),
            "task_depth": supervisor.get("task_depth"),
            "user_facts": supervisor.get("user_facts", []),
            "reference_observations": supervisor.get(
                "reference_observations", []
            ),
            "hard_constraints": supervisor.get("hard_constraints", []),
            "acceptance": supervisor.get("acceptance", []),
        }
    )


def supervisor_intake_prompt(task: str) -> str:
    """Wrap the root user input as one bounded, non-executing intake Turn."""

    return handoff(
        "This is the Supervisor's first intake Turn for a five-Thread HIA project. "
        "Your sole action is to return exactly one JSON object satisfying the current "
        "outputSchema. Produce only the bounded intake brief: summary, "
        "scene-task eligibility, task_depth, collaboration mode, User facts, Reference "
        "observations, Codex assumptions, "
        "Verified scene facts, hard constraints, and acceptance criteria. Do not use "
        "tools, inspect the repository or Houdini, research, execute or validate the "
        "task, change files or the HIP, or create, get, update, block, complete, or "
        "otherwise manage a native Goal. Do not wait for Planning and do not authorize "
        "or perform any stage; the independent Planning Thread is running in parallel. "
        "Set scene_task_eligibility.decision to eligible only when the USER TASK "
        "actually asks to create, modify, inspect, review, render, animate, simulate, "
        "or otherwise work on a Houdini scene or HIP. Plugin or repository development, "
        "project-mode validation, configuration, debugging, and general questions are "
        "not_applicable. Use unclear when the task does not establish either case, and "
        "give a short factual reason without investigating. "
        "Classify task_depth independently of the User's 单个 AI / 项目团队 routing: "
        "direct means one known deterministic edit or read; focused means one bounded "
        "subsystem or multi-node modification needing a plan and targeted evidence; "
        "full means a complete or complex asset, reference-driven result, substantive "
        "material/render/animation/simulation work, multiple semantic stages, or a "
        "high-consequence scene task. Team routing never forces full. "
        "Treat the following USER TASK as source data for this intake brief and preserve "
        "its user-authored constraints. Return no prose outside the JSON object."
        "\n\nUSER TASK:\n"
        + task
    )


def stage_prompt(supervisor: Mapping[str, Any], stage: Mapping[str, Any]) -> str:
    depth = supervisor.get("task_depth")
    depth_rule = (
        "This is a Full stage: preserve its production information floors and semantic "
        "decomposition. "
        if depth == "full"
        else "This is a bounded Direct/Focused stage: keep the complete executable card "
        "without padding it to Full length. "
    )
    return handoff(
        f"Execute exactly this complete current-stage card at task_depth={depth}. "
        + depth_rule
        + "Do not build later stages or collapse the project into one giant HOM batch. "
        "Preserve every User fact and acceptance claim over assumptions. A generic "
        "primitive or Box-heavy stand-in, renamed equivalent substitute, visible "
        "penetration, floating/unsupported assembly, or missing usable image evidence "
        "cannot complete a finished-asset stage. Return fresh claim-specific technical "
        "evidence with scope/path, frame/time, actual observation or measurement, "
        "source, result, and a stable fresh evidence id, plus real review image "
        "records with a fresh id, path, and frame/time for visual claims."
        "\n\nGLOBAL CONSTRAINT CAPSULE:\n"
        + _constraint_capsule(supervisor)
        + "\n\nCURRENT STAGE CARD:\n"
        + handoff(stage)
    )


def plan_authorization_prompt(
    brief: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> str:
    depth = brief.get("task_depth")
    depth_rule = (
        "Because task_depth is full, enforce at least three semantic stages, 10000 "
        "task-specific information units overall, 2500 per complete stage card, at "
        "least three ordered steps and 350 units per step, plus distinct visual and "
        "technical claims. "
        if depth == "full"
        else "Because task_depth is direct/focused, require complete precise stage and "
        "step fields plus both review lanes, but do not demand Full word-count, stage-"
        "count, step-count, or evidence-count floors. "
    )
    return handoff(
        f"Authorize the final task-depth blueprint at task_depth={depth} before any "
        "scene write. The authorized task_depth must exactly equal the Supervisor intake "
        "and may not change in later revisions. "
        + depth_rule
        + "Preserve User facts verbatim where precision matters; never let reference "
        "observations, Codex assumptions, or reviewer preferences override them. "
        "Independently expand any vague Planning phrase into task-specific executable "
        "steps with exact responsibility, path or network region, native operation "
        "family, connections, interacting parameters, expected result, next evidence, "
        "and minimum repair. Reject filler such as 'improve details', 'optimize the "
        "material', or 'check quality'. Complexity must serve editable professional "
        "structure and the requested style, never arbitrary nodes. Repair every missing "
        "heading, split any whole-asset or giant-HOM card, and return the complete "
        "authorized project ledgers and stages without summarizing away detail. A "
        "finished-asset primary form that remains a generic primitive or Box-heavy "
        "assembly is a concrete failure, as are unsupported contact, penetration, "
        "and stage cards without usable actual-image plus claim-specific technical "
        "evidence."
        "\n\nINITIAL SUPERVISOR BRIEF:\n"
        + handoff(brief)
        + "\n\nPLANNING PROPOSAL:\n"
        + handoff(plan)
    )


def review_prompt(
    kind: str,
    supervisor: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution: Mapping[str, Any],
    *,
    has_images: bool,
) -> str:
    image_rule = ""
    if kind == "visual":
        image_rule = (
            "Verified localImage evidence is attached; inspect it directly."
            if has_images
            else "No verified image is attached: decision must not be pass. Use "
            "not_applicable only when this stage has no visual claim."
        )
    return handoff(
        f"Perform the {kind} read-only review of this completed stage. Use supplied "
        "claim-specific evidence and attached captures. Return exactly one disposition "
        "for every original claim in this review lane; copy stage_claim exactly and "
        "cite its concrete Execution evidence ids in evidence_refs. Do not invent, "
        "merge, omit, or replace stage claims with a generic claim. Cover applicable user "
        "prohibitions and reference consistency plus silhouette/proportion, structural "
        "hierarchy, support/contact/endpoints/clearance/intersection, box-like "
        "substitution, material/light, timing, editable network structure, and errors; "
        "omit checklist ceremony for inapplicable domains. Pass only when every "
        "applicable claim is verified. State the largest consequential deviation, "
        "missing evidence, and minimum repair. Treat a generic primitive or Box-heavy "
        "finished-asset stand-in as a failed applicable claim, not a minor note. "
        f"{image_rule}"
        "\n\nGLOBAL CONSTRAINT CAPSULE:\n"
        + _constraint_capsule(supervisor)
        + "\n\nSTAGE CARD:\n"
        + handoff(stage)
        + "\n\nEXECUTION EVIDENCE:\n"
        + handoff(execution)
    )


def blocked_prompt(
    supervisor: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> str:
    return handoff(
        "The Executor reported blocked before review. Decide whether one precise "
        "repair card lets the same stage continue or whether the project is truly "
        "blocked. A blocked decision must include external_dependency with proven=true, "
        "the exact external change, observed blocker, why Codex cannot resolve it, and "
        "current Execution evidence ids. Without that proof return repair. Do not mark "
        "the stage complete."
        "\n\nGLOBAL CONSTRAINT CAPSULE:\n"
        + _constraint_capsule(supervisor)
        + "\n\nSTAGE CARD:\n"
        + handoff(stage)
        + "\n\nEXECUTOR BLOCK REPORT:\n"
        + handoff(execution)
    )


def decision_prompt(
    supervisor: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution: Mapping[str, Any],
    visual: Mapping[str, Any],
    technical: Mapping[str, Any],
) -> str:
    return handoff(
        "Decide this stage from the execution evidence and both independent reviews. "
        "Return pass only when every applicable visual and technical claim is verified; "
        "never override an unverified, failed, unjustified not-applicable, or missing "
        "claim. For repair, issue one precise current-stage "
        "repair card. Use blocked only when Codex cannot continue without external "
        "change, and then include external_dependency with proven=true, the exact "
        "external change, observed blocker, why Codex cannot resolve it, and current "
        "Execution evidence ids. Without that proof return repair. A generic primitive "
        "or Box-heavy substitute, unsupported contact, penetration, or missing usable "
        "image evidence can never receive pass.\n\nGLOBAL CONSTRAINT CAPSULE:\n"
        + _constraint_capsule(supervisor)
        + "\n\nSTAGE CARD:\n"
        + handoff(stage)
        + "\n\nEXECUTION:\n"
        + handoff(execution)
        + "\n\nVISUAL REVIEW:\n"
        + handoff(visual)
        + "\n\nTECHNICAL REVIEW:\n"
        + handoff(technical)
    )


def repair_prompt(
    supervisor: Mapping[str, Any],
    stage: Mapping[str, Any],
    execution: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    return handoff(
        "Apply only the supervisor repair card to the current stage. Remain the sole "
        "writer and reacquire affected technical measurements and real images for "
        "another independent visual and technical review; never reuse old evidence. "
        "The repair must preserve every User fact, reference constraint, prohibition, "
        "and acceptance claim in the global capsule."
        "\n\nGLOBAL CONSTRAINT CAPSULE:\n"
        + _constraint_capsule(supervisor)
        + "\n\nSTAGE CARD:\n"
        + handoff(stage)
        + "\n\nPREVIOUS EXECUTION:\n"
        + handoff(execution)
        + "\n\nREPAIR CARD:\n"
        + handoff(decision.get("repair_card", ""))
    )
