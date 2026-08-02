from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


REPOSITORY_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "bridge"))

from hia_bridge.errors import BridgeError, CodexRPCError  # noqa: E402
from hia_bridge.events import EventBuffer  # noqa: E402
from hia_bridge.project_thread_contract import (  # noqa: E402
    EXECUTION_SCHEMA,
    PLANNING_SCHEMA,
    PROJECT_HIA_DISABLE_CONFIG,
    PROJECT_HIA_ENABLE_CONFIG,
    PROJECT_ROLE_ORDER,
    REVIEW_SCHEMA,
    STAGE_CARD_SCHEMA,
    SUPERVISOR_BLOCKED_SCHEMA,
    SUPERVISOR_DECISION_SCHEMA,
    SUPERVISOR_PLAN_SCHEMA,
    SUPERVISOR_SCHEMA,
    blocked_prompt,
    decision_prompt,
    handoff,
    plan_authorization_prompt,
    repair_prompt,
    review_prompt,
    role_instructions,
    stage_prompt,
)
from hia_bridge.project_threads import (  # noqa: E402
    PROJECT_TEAM_SCHEMA,
    PROJECT_TEAM_SETTINGS_SCHEMA,
    ProjectTeamSettings,
    ProjectThreadCoordinator,
    _PendingTurn,
    project_tool_evidence,
)
from hia_bridge.session import BridgeSession  # noqa: E402


def _brief() -> dict[str, Any]:
    return {
        "scene_task_eligibility": {
            "decision": "eligible",
            "reason": "The user asks to build a Houdini scene asset.",
        },
        "summary": "建造一座完整、可编辑、可验收的木屋。",
        "task_depth": "full",
        "collaboration_mode": "serial-fallback",
        "user_facts": ["User fact: 用户要求建一个木屋。"],
        "reference_observations": [],
        "codex_assumptions": ["Codex assumption: 未给尺寸，采用可逆的小型单层比例。"],
        "verified_scene_facts": [],
        "hard_constraints": ["保留程序化可编辑结构。"],
        "acceptance": ["轮廓可辨认为木屋并有真实技术与视觉证据。"],
    }


def _stage(title: str, path: str, variant: str = "structure") -> dict[str, Any]:
    technical_claims = [
        "验证木屋权威输出路径、关键节点连接、错误状态与宽度参数传播均与阶段卡一致。",
        "测量门洞贯通、墙角闭合、屋顶支撑接触和结构净空，不得存在未声明穿插。",
    ]
    visual_claims = [
        "透视图必须清晰显示木屋门洞、居中屋脊、连续屋檐和四角真实接触关系。",
        "正面图必须证明木屋不是盒状替代，墙板层次、支撑逻辑和双坡轮廓均可辨认。",
    ]
    stage = {
        "title": title,
        "objective": "在不触碰后续材质阶段的前提下完成可辨认且可编辑的木屋承重壳体。",
        "prerequisites": ["确认 /obj 下没有 CABIN_BUILD 名称冲突。"],
        "inputs": ["User fact: 建一个木屋。", "单位为米，Y 轴向上。"],
        "ordered_construction_steps": [
            {
                "responsibility": "建立参数化墙体、门洞和双坡屋顶主结构。",
                "path_or_network_region": path,
                "native_operation_family": "Box/PolyExtrude/Boolean/Copy to Points 与原生 SOP 连接。",
                "inputs_and_connections": "尺寸控制驱动四面墙；门洞分支接入 Boolean，墙体输出接屋顶支撑分支。",
                "key_parameters": "wall_height=2.6m、wall_thickness=0.14m、roof_pitch=35deg；门宽依赖正面墙宽。",
                "expected_result": "正交视图中墙体连续、门洞贯通、屋脊居中且屋檐有一致挑出。",
                "next_step_evidence": "读取节点连接与参数，并提供正面和透视实际截图后才进入下一步。",
                "failure_minimum_repair": "只修正门洞分支、屋顶坡度驱动或发生穿插的最小局部，不重建已通过墙体。",
            }
            ,
            {
                "responsibility": "在已验证墙体输出上建立独立屋顶支撑、椽条端点和门框接触关系。",
                "path_or_network_region": path + "/ROOF_AND_FRAME_SUPPORT",
                "native_operation_family": "Line/Resample/Sweep/Copy to Points/Boolean 与原生 SOP 属性驱动。",
                "inputs_and_connections": "OUT_WALLS 接入屋顶跨度控制输入，脊线分支输出连接 Sweep，门框分支经 Merge 接回结构输出。",
                "key_parameters": "rafter_spacing=0.42m 依赖 cabin_width；eave_offset=0.28m 驱动椽条端点，frame_depth 依赖 wall_thickness。",
                "expected_result": "每根椽条端点落在墙顶支撑区，门框接触地面与横梁，屋檐挑出一致且没有悬空。",
                "next_step_evidence": "用 hia_inspect 回读脊线与 Sweep 连接、端点坐标和净空测量，再获取 frame 1 的正面与透视捕获。",
                "failure_minimum_repair": "若端点或门框接触失败，只改对应 Resample 数量、Sweep 截面或 frame_depth 依赖并重新测量。",
            },
        ],
        "native_node_strategy": ["优先现有节点；新建标准原生 SOP，HOM 仅编排这一语义批次。"],
        "authoring_batches": ["批次一建控制与墙体；批次二建门洞和屋顶并分别回读。"],
        "parameter_dependencies": ["cabin_width 驱动墙长、门位与屋脊跨度，变更后必须验证三者同步。"],
        "outputs": ["/obj/CABIN_BUILD/OUT_STRUCTURE 为本阶段唯一权威输出。"],
        "visible_characteristics": ["四墙围合、明确门洞、对称双坡屋顶、可读屋檐和真实接触关系。"],
        "structural_relationships": ["屋顶由两侧墙顶支撑，屋脊居中；墙角闭合且门框不得悬空。"],
        "prohibitions": ["禁止用单个盒子冒充木屋，禁止全资产巨型 HOM，禁止并行写 HIP。"],
        "technical_evidence": technical_claims,
        "visual_evidence": visual_claims,
        "reviewers": ["Visual Review 检查实际图像；Technical Review 检查路径、连接和依赖。"],
        "failure_minimum_repair": ["选择最大偏差，只改最小相关网络区并重新获取两类证据。"],
        "downstream_contract": ["下一阶段只依赖 OUT_STRUCTURE、米制变换和 wall/roof 参数接口。"],
        "evidence_disposition": {
            "technical": "pending",
            "visual": "pending",
            "stage": "not started",
        },
    }
    phase = {
        "structure": (
            "墙脚板与楼板托梁",
            "Curve/Resample/Sweep/Copy to Points",
            "joist_spacing=0.4m 依赖 cabin_depth，sill_offset=0.12m 依赖 wall_thickness",
            "托梁沿进深均匀排列并与墙脚板形成可测量接触，四角没有悬空或重叠",
        ),
        "envelope": (
            "墙板分层与门窗洞口收边",
            "PolyExtrude/Boolean/Group Expression/UV Flatten",
            "board_width=0.18m 驱动分缝，trim_width=0.09m 依赖 opening_width",
            "木屋立面形成真实板缝、独立收边与连续防雨层，洞口边缘没有盒状替代",
        ),
        "joinery": (
            "檐口、角柱与可编辑木构节点",
            "Line/Sweep/Attribute Adjust/Copy to Points/Match Size",
            "post_size=0.16m 依赖 wall_height，fascia_drop=0.11m 依赖 roof_pitch",
            "角柱承担墙角视觉支撑，檐口端点一致，构件层级与连接方向清晰可追踪",
        ),
        "lookdev": (
            "木材材质、灯光层次与最终展示",
            "MaterialX Standard Surface/Ramp/Noise/UV Transform/Karma Light",
            "grain_scale=0.22 依赖真实米制 UV，key_fill_ratio=3:1 依赖屋檐阴影深度",
            "木纹尺度与构件方向一致，暖冷光层次服务双坡轮廓且不掩盖结构接触",
        ),
    }[variant]
    subject, operations, parameters, expected = phase
    for step_index, step in enumerate(stage["ordered_construction_steps"], 1):
        step["responsibility"] += f" 本步骤仅负责{subject}的第 {step_index} 个可验证边界。"
        step["path_or_network_region"] += f"/{variant.upper()}_BOUNDARY_{step_index}"
        step["native_operation_family"] += f" 对{subject}只采用 {operations} 中与当前边界直接相关的原生操作。"
        step["inputs_and_connections"] += f" {variant.upper()}_INPUT_{step_index} 连接该边界控制输入，结果输出到 {variant.upper()}_CHECK_{step_index}。"
        step["key_parameters"] += f" 同时验证 {parameters}，参数变化必须传播到当前输出。"
        step["expected_result"] += " " + expected + "，不得用任意复杂度掩盖失败接触。"
        step["next_step_evidence"] += f" 证据必须标出{subject}对应 tool item、路径、frame 1 与实际观察。"
        step["failure_minimum_repair"] += f" 修复后只重跑{subject}受影响分支并生成新证据 ID。"
    stage["ordered_construction_steps"].extend(
        [
            {
                "responsibility": f"在 {path} 内单独完成{subject}的可编辑构造，不改写已通过的上游权威输出。",
                "path_or_network_region": f"{path}/{variant.upper()}_DETAILS",
                "native_operation_family": f"{operations} 原生操作族，只为{subject}的结构和可编辑性服务。",
                "inputs_and_connections": f"把本阶段入口 OUT_INPUT 接入 {variant.upper()}_DETAILS，控制分支连接主构造输入，经过独立 Merge 后输出到 OUT_{variant.upper()}，不得跨接后续阶段。",
                "key_parameters": f"{parameters}；所有依赖由上游命名控制参数驱动并在变更后逐项回读。",
                "expected_result": expected + "，同时保持用户要求的木屋风格而不增加无效节点。",
                "next_step_evidence": f"记录 OUT_{variant.upper()} 路径、实际输入连接、上述参数值与 frame 1 测量，并捕获能清楚看到{subject}的透视证据。",
                "failure_minimum_repair": f"若{subject}的接触、比例或依赖不成立，只修 {variant.upper()}_DETAILS 内对应控制或连接，重新生成新 tool item 与新捕获。",
            },
            {
                "responsibility": f"对{subject}执行阶段边界整理与权威输出封装，使下游只消费明确接口。",
                "path_or_network_region": f"{path}/OUT_{variant.upper()}_PUBLISH",
                "native_operation_family": "Null/Attribute Promote/Group/Name 与标准 SOP 输出封装，不创建装饰性冗余网络。",
                "inputs_and_connections": f"OUT_{variant.upper()} 连接发布 Null；诊断分支只读连接验证输入，权威输出继续向下游阶段，禁止反向线和悬空实验分支。",
                "key_parameters": f"publish_variant={variant}、units=meter、frame=1；输出命名依赖阶段标题，诊断阈值依赖{subject}的真实尺度。",
                "expected_result": f"下游能从唯一 OUT_{variant.upper()}_PUBLISH 读取{subject}，网络自上而下、命名清晰、没有默认名或无效复杂度。",
                "next_step_evidence": f"读取发布节点的类型、路径、输入索引、上游错误与边界包围盒；在视觉证据中标明{subject}对木屋整体轮廓的贡献。",
                "failure_minimum_repair": f"若发布接口或网络组织失败，仅重连 OUT_{variant.upper()}_PUBLISH 周边并清除受影响的悬空分支，不重建已验证几何。",
            },
        ]
    )
    stage["native_node_strategy"].append(
        f"{subject}采用 {operations} 的最小专业组合；每个节点都必须解释其结构、材质或证据用途。"
    )
    stage["authoring_batches"].append(
        f"先作者化{subject}主数据流并回读，再单独封装 OUT_{variant.upper()}_PUBLISH 与证据，不跨阶段写入。"
    )
    stage["parameter_dependencies"].append(parameters + "，更改任一主控后重新验证受影响连接和视觉结果。")
    stage["outputs"].append(f"{path}/OUT_{variant.upper()}_PUBLISH 是{subject}的下游唯一接口。")
    stage["visible_characteristics"].append(expected + "，专业复杂度必须直接服务木屋识别与编辑。")
    stage["structural_relationships"].append(f"{subject}与上游 OUT_INPUT 接触并由明确参数依赖驱动，不得悬空或穿插。")
    stage["failure_minimum_repair"].append(f"优先定位{subject}的最大后果偏差，只修对应分支并重新获取两类新证据。")
    stage["downstream_contract"].append(f"下游只可读取 OUT_{variant.upper()}_PUBLISH 与公开控制参数，不依赖内部临时节点。")
    return stage


def _plan(*, title: str = "木屋施工项目", two_stages: bool = False) -> dict[str, Any]:
    del two_stages
    stages = [
        _stage("搭建木屋承重壳体与楼板基础", "/obj/CABIN_BUILD", "structure"),
        _stage("建立木屋墙板洞口与防雨外壳", "/obj/CABIN_ENVELOPE", "envelope"),
        _stage("完善木屋檐口角柱与木构节点", "/obj/CABIN_JOINERY", "joinery"),
        _stage("建立木屋材质灯光与展示层次", "/obj/CABIN_LOOKDEV", "lookdev"),
    ]
    return {
        "project_title": title,
        "summary": "面向稀疏输入扩写的完整、任务特异木屋施工蓝图。",
        "task_depth": "full",
        "collaboration_mode": "serial-fallback",
        "user_facts": ["User fact: 用户要求建一个木屋。"],
        "reference_observations": [],
        "codex_assumptions": ["Codex assumption: 使用可逆的小型单层比例，等待证据修订。"],
        "verified_scene_facts": [],
        "hard_constraints": [
            "保留程序化可编辑结构。",
            "不使用占位盒替代完成品；保持原生节点可编辑。",
        ],
        "acceptance": [
            "轮廓可辨认为木屋并有真实技术与视觉证据。",
            "结构、比例、连接、参数传播和实际图像均通过独立审查。",
        ],
        "stages": stages,
    }


def _vague_plan() -> dict[str, Any]:
    stage = {
        "title": "完善细节",
        "objective": "检查质量",
        **{
            field: ["按需优化"]
            for field in (
                "prerequisites",
                "inputs",
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
            )
        },
        "ordered_construction_steps": [
            {
                field: "按需优化"
                for field in (
                    "responsibility",
                    "path_or_network_region",
                    "native_operation_family",
                    "inputs_and_connections",
                    "key_parameters",
                    "expected_result",
                    "next_step_evidence",
                    "failure_minimum_repair",
                )
            }
        ],
        "evidence_disposition": {
            "technical": "pending",
            "visual": "pending",
            "stage": "not started",
        },
    }
    return {
        "project_title": "木屋",
        "summary": "检查质量",
        "task_depth": "full",
        "collaboration_mode": "serial-fallback",
        "user_facts": ["User fact: 用户要求建一个木屋。"],
        "reference_observations": [],
        "codex_assumptions": ["Codex assumption: 按需处理。"],
        "verified_scene_facts": [],
        "hard_constraints": ["保留程序化可编辑结构。"],
        "acceptance": ["检查质量"],
        "stages": [stage],
    }


def _bounded_depth_case(
    depth: str,
    task: str,
    *,
    steps: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a complete non-Full brief/plan without Full production padding."""

    brief = _brief()
    brief["task_depth"] = depth
    brief["summary"] = f"按 {depth} 深度完成用户指定的有界 Houdini 修改并验收。"
    brief["user_facts"].append("User task verbatim:\n" + task)

    plan = _plan(title=f"{depth.title()} Houdini 修改")
    plan["task_depth"] = depth
    plan["summary"] = f"仅处理这一项 {depth} 范围修改，保留精确路径、参数和双审查证据。"
    plan["user_facts"] = copy.deepcopy(brief["user_facts"])
    plan["hard_constraints"] = copy.deepcopy(brief["hard_constraints"])
    plan["acceptance"] = copy.deepcopy(brief["acceptance"])
    stage = copy.deepcopy(plan["stages"][0])
    stage["title"] = "完成并验证当前有界修改"
    stage["objective"] = task + "；只修改指定网络区域并回读实际结果。"
    stage["inputs"].append("Authoritative User task: " + task)
    stage["ordered_construction_steps"] = stage["ordered_construction_steps"][:steps]
    stage["ordered_construction_steps"][0]["responsibility"] += (
        " 当前唯一责任是执行并验证该 User task：" + task
    )
    plan["stages"] = [stage]
    return brief, plan


def _execution(
    images: list[str] | None = None,
    *,
    outcome: str = "completed",
    evidence_suffix: str = "",
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "summary": "当前阶段已按卡执行。" if outcome == "completed" else "当前阶段无法安全继续。",
        "collaboration_mode": "serial-fallback",
        "technical_evidence": [
            {
                "id": "tech-output-1" + evidence_suffix,
                "tool_item_id": "tool-inspect-1" + evidence_suffix,
                "claim": "木屋壳体输出、连接和参数依赖可编辑且无结构穿插。",
                "scope_or_path": "/obj/CABIN_BUILD/OUT_STRUCTURE",
                "frame_or_time": "frame 1",
                "observation_or_measurement": "门洞贯通，屋脊居中；宽度变化同步驱动墙体和屋顶跨度。",
                "source": "hia_inspect item result and measured scene facts",
                "result": "verified" if outcome == "completed" else "blocked",
            },
            {
                "id": "tech-measure-2" + evidence_suffix,
                "tool_item_id": "tool-context-2" + evidence_suffix,
                "claim": "门洞、墙角、屋顶支撑和端点净空均有实际测量。",
                "scope_or_path": "/obj/CABIN_BUILD/ROOF_AND_FRAME_SUPPORT",
                "frame_or_time": "frame 1",
                "observation_or_measurement": "椽条端点落在墙顶，门框落地，最小净空 0.08m，无未声明穿插。",
                "source": "hia_context item result and measured scene facts",
                "result": "verified" if outcome == "completed" else "blocked",
            },
        ],
        "remaining": [] if outcome == "completed" else ["需要监督裁定。"],
        "review_images": [
            {
                "id": f"image-{index + 1}{evidence_suffix}",
                "capture_tool_item_id": f"tool-capture-{index + 1}{evidence_suffix}",
                "path": str(path),
                "frame_or_time": "frame 1",
            }
            for index, path in enumerate(images or [])
        ],
    }


def _review(kind: str, evidence_suffix: str = "") -> dict[str, Any]:
    stage = _stage("搭建可编辑木屋壳体", "/obj/CABIN_BUILD")
    stage_claims = stage["visual_evidence" if kind == "visual" else "technical_evidence"]
    references = (
        ["image-1" + evidence_suffix]
        if kind == "visual"
        else ["tech-output-1" + evidence_suffix, "tech-measure-2" + evidence_suffix]
    )
    actual = (
        [
            "透视图中木屋门洞贯通、屋脊居中，连续屋檐与四角墙脚真实接触清晰可见。",
            "正面图可见木屋独立墙板层次、承重支撑和双坡轮廓，明确不是盒状替代。",
        ]
        if kind == "visual"
        else [
            "在 /obj/CABIN_BUILD/OUT_STRUCTURE 回读木屋权威输出路径、关键节点连接、错误状态与宽度参数传播，均与阶段卡一致。",
            "frame 1 测量木屋门洞贯通、墙角闭合、屋顶支撑接触与结构净空，最小净空 0.08m 且无未声明穿插。",
        ]
    )
    return {
        "decision": "pass",
        "collaboration_mode": "serial-fallback",
        "claims": [
            {
                "stage_claim": stage_claim,
                "disposition": "verified",
                "evidence_refs": (
                    references
                    if kind == "visual"
                    else [references[index]]
                ),
                "actual_evidence": [actual[index]],
                "deviation": "无后果偏差。",
                "minimum_repair": "无需修复。",
                "not_applicable_reason": "",
            }
            for index, stage_claim in enumerate(stage_claims)
        ],
        "largest_consequential_deviation": "无。",
        "missing_evidence": [],
        "repair": [],
    }


def _repair_review(kind: str) -> dict[str, Any]:
    value = _review(kind)
    value["decision"] = "repair"
    value["missing_evidence"] = ["当前 Execution Turn 缺少可引用的真实证据。"]
    value["repair"] = ["由 Execution 获取本阶段新的真实 HIA 证据后重新审查。"]
    for claim in value["claims"]:
        claim["disposition"] = "unverified"
        claim["evidence_refs"] = []
        claim["actual_evidence"] = []
        claim["deviation"] = "缺少当前轮证据，不能验证。"
        claim["minimum_repair"] = "获取当前阶段新证据。"
    return value


def _generic_review(kind: str) -> dict[str, Any]:
    value = _review(kind)
    value["claims"] = [
        {
            "stage_claim": "泛泛地检查所有质量",
            "disposition": "verified",
            "evidence_refs": ["image-1" if kind == "visual" else "tech-output-1"],
            "actual_evidence": ["看起来已经检查。"],
            "deviation": "无。",
            "minimum_repair": "无。",
            "not_applicable_reason": "",
        }
    ]
    return value


class _ScriptedClient:
    def __init__(
        self,
        *,
        brief: Mapping[str, Any] | None = None,
        planning: Mapping[str, Any] | None = None,
        planning_sequence: list[Mapping[str, Any]] | None = None,
        authorization: Mapping[str, Any] | None = None,
        authorization_sequence: list[Mapping[str, Any]] | None = None,
        execution: Mapping[str, Any] | None = None,
        execution_sequence: list[Mapping[str, Any]] | None = None,
        visual: Mapping[str, Any] | None = None,
        visual_sequence: list[Mapping[str, Any]] | None = None,
        technical: Mapping[str, Any] | None = None,
        technical_sequence: list[Mapping[str, Any]] | None = None,
        decision_sequence: list[Mapping[str, Any]] | None = None,
        blocked_decision: Mapping[str, Any] | None = None,
        block: set[str] | None = None,
        root_thread_id: str = "thread-root",
        response_model: str | None = None,
        response_effort: str | None = None,
        response_service_tier: str | None = None,
    ) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.roles = {root_thread_id: "supervisor"}
        self.root_thread_id = root_thread_id
        self.response_model = response_model
        self.response_effort = response_effort
        self.response_service_tier = response_service_tier
        self.brief = copy.deepcopy(dict(brief or _brief()))
        self.planning = dict(planning or _plan())
        self.planning_sequence = [
            dict(value) for value in (planning_sequence or [])
        ]
        self.authorization = dict(authorization or _plan())
        self.authorization_sequence = [
            dict(value) for value in (authorization_sequence or [])
        ]
        self.execution = dict(execution or _execution())
        self.execution_sequence = [dict(value) for value in (execution_sequence or [])]
        self.visual = dict(visual or _review("visual"))
        self.visual_sequence = [dict(value) for value in (visual_sequence or [])]
        self.technical = dict(technical or _review("technical"))
        self.technical_sequence = [dict(value) for value in (technical_sequence or [])]
        self.decision_sequence = [dict(value) for value in (decision_sequence or [])]
        self.blocked_decision = dict(
            blocked_decision
            or {
                "decision": "blocked",
                "summary": "需要外部改变。",
                "repair_card": "",
                "collaboration_mode": "serial-fallback",
                "external_dependency": {
                    "proven": True,
                    "required_external_change": "用户或管理员必须恢复当前不可达的 Houdini 服务。",
                    "observed_blocker": "当前 HIA_SESSION_UNAVAILABLE 错误记录 Houdini service unavailable 且 session unreachable。",
                    "why_codex_cannot_resolve": "Codex 不能在当前 session unreachable 状态下恢复用户的 Houdini service。",
                    "evidence_refs": ["tool-inspect-1"],
                },
            }
        )
        self.block = set(block or ())
        self._sink: Any = None
        self._counter = 0
        self._active: dict[tuple[str, str], str] = {}
        self.goals: dict[str, dict[str, Any]] = {}
        self.task_text: str | None = None
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        return True

    @property
    def process_id(self) -> int:
        return 4242

    def set_event_sink(self, sink: Any) -> None:
        self._sink = sink

    def request_with_timeout(
        self, method: str, params: dict[str, Any], *, timeout_seconds: float
    ) -> Any:
        del timeout_seconds
        return self.request(method, params)

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        copied = copy.deepcopy(dict(params))
        with self._lock:
            self.requests.append((method, copied))
            if method == "thread/start":
                source = copied.get("threadSource")
                if isinstance(source, str):
                    role = source.rsplit("/", 1)[-1]
                    thread_id = "thread-" + role
                else:
                    role, thread_id = "supervisor", self.root_thread_id
                self.roles[thread_id] = role
                response = {
                    "thread": {
                        "id": thread_id,
                        "turns": [],
                    }
                }
                response["model"] = self.response_model or copied.get("model")
                response["reasoningEffort"] = (
                    self.response_effort or copied.get("reasoningEffort")
                )
                response["serviceTier"] = (
                    self.response_service_tier or copied.get("serviceTier")
                )
                return response
            if method == "thread/resume":
                return {
                    "thread": {"id": copied["threadId"], "turns": []},
                    "model": self.response_model or copied.get("model"),
                    "reasoningEffort": self.response_effort
                    or copied.get("reasoningEffort"),
                    "serviceTier": self.response_service_tier
                    or copied.get("serviceTier"),
                }
            if method == "thread/name/set":
                return {}
            if method == "thread/goal/set":
                goal = {
                        "threadId": copied["threadId"],
                        "objective": copied["objective"],
                        "status": copied["status"],
                        "tokenBudget": copied["tokenBudget"],
                }
                self.goals[copied["threadId"]] = goal
                return {"goal": copy.deepcopy(goal)}
            if method == "thread/goal/get":
                return {"goal": copy.deepcopy(self.goals.get(copied["threadId"]))}
            if method == "turn/steer":
                return {"turnId": copied["expectedTurnId"]}
            if method == "turn/interrupt":
                thread_id, turn_id = copied["threadId"], copied["turnId"]
                self._active.pop((thread_id, turn_id), None)
                self._emit_completed(thread_id, turn_id, None, "interrupted")
                return {}
            if method == "thread/read":
                thread_id = copied["threadId"]
                active_turns = [
                    turn_id
                    for (active_thread_id, turn_id), _role in self._active.items()
                    if active_thread_id == thread_id
                ]
                return {
                    "thread": {
                        "id": thread_id,
                        "status": {
                            "type": "active" if active_turns else "idle"
                        },
                        "turns": [
                            {
                                "id": turn_id,
                                "status": "inProgress",
                                "items": [],
                            }
                            for turn_id in active_turns
                        ],
                    }
                }
            if method != "turn/start":
                raise AssertionError(f"Unexpected request: {method}")
            self._counter += 1
            thread_id = copied["threadId"]
            turn_id = f"turn-{self._counter}"
            role = self.roles[thread_id]
            root_turn = (
                thread_id == self.root_thread_id
                and copied.get("outputSchema") == SUPERVISOR_SCHEMA
            )
            if root_turn and self.task_text is None:
                root_text = copied.get("input", [{}])[0].get("text", "")
                marker = "\n\nUSER TASK:\n"
                if isinstance(root_text, str) and marker in root_text:
                    self.task_text = root_text.rsplit(marker, 1)[-1]
            block_key = "root" if root_turn else role
            self._active[(thread_id, turn_id)] = block_key
            if block_key not in self.block:
                payload = self._payload(role, copied, root_turn)
                self._active.pop((thread_id, turn_id), None)
                self._emit_completed(thread_id, turn_id, payload, "completed")
            return {"turn": {"id": turn_id, "status": "inProgress"}}

    def complete_root(self, turn_id: str = "turn-root") -> None:
        self._emit_completed(
            self.root_thread_id,
            turn_id,
            copy.deepcopy(self.brief),
            "completed",
        )

    def _payload(
        self, role: str, params: Mapping[str, Any], root_turn: bool
    ) -> Mapping[str, Any]:
        if root_turn:
            return copy.deepcopy(self.brief)
        prompt = params["input"][0]["text"]
        if role == "planning":
            return self._with_task_fact(
                self.planning_sequence.pop(0)
                if self.planning_sequence
                else self.planning,
                prompt,
            )
        if role == "execution":
            return self.execution_sequence.pop(0) if self.execution_sequence else self.execution
        if role == "visual_review":
            return self.visual_sequence.pop(0) if self.visual_sequence else self.visual
        if role == "technical_review":
            return self.technical_sequence.pop(0) if self.technical_sequence else self.technical
        if "Executor reported blocked" in prompt:
            return self.blocked_decision
        if "Authorize the final" in prompt:
            return self._with_task_fact(
                self.authorization_sequence.pop(0)
                if self.authorization_sequence
                else self.authorization,
                prompt,
            )
        if "previous authorization is not executable" in prompt:
            return self._with_task_fact(
                self.authorization_sequence.pop(0)
                if self.authorization_sequence
                else self.authorization,
                prompt,
            )
        if self.decision_sequence:
            return self.decision_sequence.pop(0)
        return {
            "decision": "pass",
            "summary": "两路证据支持通过。",
            "repair_card": "",
            "collaboration_mode": "serial-fallback",
        }

    def _with_task_fact(
        self,
        value: Mapping[str, Any],
        prompt: str = "",
    ) -> dict[str, Any]:
        output = copy.deepcopy(dict(value))
        facts = list(output.get("user_facts", []))
        if self.task_text:
            normalized_task = ProjectThreadCoordinator._normalized_verbatim_text(
                self.task_text
            )
            if normalized_task and not any(
                normalized_task
                in ProjectThreadCoordinator._normalized_verbatim_text(fact)
                for fact in facts
                if isinstance(fact, str)
            ):
                facts.append("User task verbatim:\n" + self.task_text)
        for match in re.finditer(r'"user_text":("(?:\\.|[^"\\])*")', prompt):
            try:
                guidance = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(guidance, str) or not guidance.strip():
                continue
            guidance_fact = "User guidance verbatim:\n" + guidance
            normalized = ProjectThreadCoordinator._normalized_verbatim_text(
                guidance_fact
            )
            if normalized not in {
                ProjectThreadCoordinator._normalized_verbatim_text(fact)
                for fact in facts
                if isinstance(fact, str)
            }:
                facts.append(guidance_fact)
        for match in re.finditer(
            r'("User guidance verbatim:\\n(?:\\.|[^"\\])*")',
            prompt,
        ):
            try:
                guidance_fact = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            normalized = ProjectThreadCoordinator._normalized_verbatim_text(
                guidance_fact
            )
            if normalized not in {
                ProjectThreadCoordinator._normalized_verbatim_text(fact)
                for fact in facts
                if isinstance(fact, str)
            }:
                facts.append(guidance_fact)
        output["user_facts"] = facts
        return output

    def _emit_completed(
        self,
        thread_id: str,
        turn_id: str,
        payload: Mapping[str, Any] | None,
        status: str,
    ) -> None:
        sink = self._sink
        if not callable(sink):
            return
        sink(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": "inProgress"},
                },
            }
        )
        if payload is not None:
            if payload.get("collaboration_mode") == "used-with-real-events":
                sink(
                    {
                        "type": "codex_notification",
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "id": "collab-" + turn_id,
                                "type": "collabAgentToolCall",
                                "tool": "spawnAgent",
                                "status": "completed",
                                "senderThreadId": thread_id,
                                "receiverThreadIds": ["internal-child-" + turn_id],
                            },
                        },
                    }
                )
            if isinstance(payload.get("technical_evidence"), list):
                for evidence in payload["technical_evidence"]:
                    item_id = evidence["tool_item_id"]
                    match = re.search(r"hia_[a-z0-9_]+", evidence["source"])
                    tool = match.group(0) if match else "hia_inspect"
                    blocked = payload.get("outcome") == "blocked"
                    sink(
                        {
                            "type": "codex_notification",
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": item_id,
                                    "type": "mcpToolCall",
                                    "tool": tool,
                                    "status": "failed" if blocked else "completed",
                                    "arguments": {
                                        "path": evidence["scope_or_path"],
                                        "frame": 1,
                                    },
                                    "result": {
                                        "structuredContent": {
                                            "ok": not blocked,
                                            **(
                                                {
                                                    "error": {
                                                        "code": "HIA_SESSION_UNAVAILABLE",
                                                        "message": "Houdini service unavailable; current session unreachable",
                                                    }
                                                }
                                                if blocked
                                                else {
                                                    "result": {
                                                        "path": evidence["scope_or_path"],
                                                        "frame": 1,
                                                        "observation": evidence[
                                                            "observation_or_measurement"
                                                        ],
                                                        "validation": {
                                                            "status": evidence["result"]
                                                        },
                                                        "created_or_changed_paths": [],
                                                        "warnings": [],
                                                        "errors": [],
                                                    }
                                                }
                                            ),
                                        }
                                    },
                                },
                            },
                        }
                    )
                for image in payload.get("review_images", []):
                    path = Path(image["path"]).resolve()
                    source_hip = None
                    if path.parent.name == "screenshots" and path.parent.parent.name == ".hia":
                        candidates = sorted(path.parent.parent.parent.glob("*.hip*"))
                        source_hip = str(candidates[0].resolve()) if candidates else None
                        scope = "hip"
                    else:
                        scope = "runtime_fallback"
                    sink(
                        {
                            "type": "codex_notification",
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": image["capture_tool_item_id"],
                                    "type": "mcpToolCall",
                                    "tool": "hia_capture_viewport",
                                    "status": "completed",
                                    "arguments": {"frame": 1, "return_image": True},
                                    "result": {
                                        "structuredContent": {
                                            "ok": True,
                                            "result": {
                                                "absolute_path": str(path),
                                                "storage_scope": scope,
                                                "source_hip_path": source_hip,
                                                "actual_frame": 1,
                                            },
                                        }
                                    },
                                },
                            },
                        }
                    )
            sink(
                {
                    "type": "codex_notification",
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "id": "message-" + turn_id,
                            "type": "agentMessage",
                            "text": json.dumps(payload, ensure_ascii=False),
                        },
                    },
                }
            )
        sink(
            {
                "type": "codex_notification",
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": turn_id, "status": status},
                },
            }
        )


class _ProjectHarness:
    def __init__(self, client: _ScriptedClient) -> None:
        self.client = client
        self.coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            client,
            EventBuffer(),
            turn_timeout_seconds=1.0,
        )
        client.set_event_sink(self.coordinator.handle_client_event)

    def start(self, task: str = "建一个木屋") -> str:
        spec = self.coordinator.prepare_project("thread-root", task)
        self.coordinator.begin_root_turn_request(spec["project_id"])
        self.client.complete_root()
        self.coordinator.attach_root_turn(spec["project_id"], "turn-root")
        return spec["project_id"]

    def snapshot(self) -> dict[str, Any]:
        return self.coordinator.snapshot(
            mode="team",
            writable=True,
            settings_state_status="memory",
        )

    def wait_terminal(self, timeout: float = 3.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            project = self.snapshot()["projects"][0]
            if project["status"] != "running":
                return project
            time.sleep(0.01)
        self.coordinator.interrupt_project()
        self.fail("Project did not reach a terminal result")

    def fail(self, message: str) -> None:
        raise AssertionError(message)


class ProjectBlueprintContractTests(unittest.TestCase):
    def test_task_depth_is_mandatory_and_team_choice_does_not_force_full(self) -> None:
        self.assertIn("task_depth", SUPERVISOR_SCHEMA["required"])
        self.assertIn("task_depth", PLANNING_SCHEMA["required"])
        self.assertIn("task_depth", SUPERVISOR_PLAN_SCHEMA["required"])
        self.assertEqual(
            ["direct", "focused", "full"],
            SUPERVISOR_SCHEMA["properties"]["task_depth"]["enum"],
        )

        task = "把 /obj/CABIN_BUILD/wall_height 参数改为 2.8，并回读确认。"
        brief, plan = _bounded_depth_case("direct", task)
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            image = Path(directory) / "direct-wall-height.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            client = _ScriptedClient(
                brief=brief,
                planning=plan,
                authorization=plan,
                execution=_execution([str(image)]),
            )
            harness = _ProjectHarness(client)
            harness.start(task)
            project = harness.wait_terminal()

        self.assertEqual("completed", project["status"])
        starts = [
            (client.roles[params["threadId"]], params)
            for method, params in client.requests
            if method == "turn/start"
        ]
        self.assertEqual(1, sum(role == "execution" for role, _ in starts))
        self.assertEqual(1, sum(role == "visual_review" for role, _ in starts))
        self.assertEqual(1, sum(role == "technical_review" for role, _ in starts))
        execution_prompt = next(
            params["input"][0]["text"]
            for role, params in starts
            if role == "execution"
        )
        self.assertIn("task_depth=direct", execution_prompt)
        self.assertNotIn("10000", execution_prompt)

    def test_focused_bounded_multi_node_plan_passes_without_full_floors(self) -> None:
        task = (
            "在 /obj/CABIN_BUILD/ROOF_AND_FRAME_SUPPORT 内重连屋脊 Sweep、"
            "门框 Merge 与 OUT_STRUCTURE，并验证依赖。"
        )
        brief, plan = _bounded_depth_case("focused", task, steps=2)
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )

        self.assertEqual([], coordinator._validate_plan(plan, task, brief))
        self.assertEqual(1, len(plan["stages"]))
        self.assertEqual(2, len(plan["stages"][0]["ordered_construction_steps"]))
        self.assertLess(coordinator._information_units(plan), 10_000)

    def test_planning_depth_can_correct_to_brief_but_authorized_depth_is_fixed(self) -> None:
        task = "把 /obj/CABIN_BUILD/wall_height 参数改为 2.8，并回读确认。"
        brief, corrected = _bounded_depth_case("direct", task)
        planning = _plan()
        planning["user_facts"] = copy.deepcopy(brief["user_facts"])
        planning["hard_constraints"] = copy.deepcopy(brief["hard_constraints"])
        planning["acceptance"] = copy.deepcopy(brief["acceptance"])
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )

        mismatch = coordinator._validate_plan(planning, task, brief)
        self.assertTrue(any("task_depth must exactly match" in error for error in mismatch))
        self.assertEqual([], coordinator._validate_plan(corrected, task, brief))
        self.assertEqual(
            [],
            coordinator._plan_regression_errors(
                planning,
                corrected,
                allow_depth_correction=True,
            ),
        )

        changed_after_authorization = copy.deepcopy(corrected)
        changed_after_authorization["task_depth"] = "focused"
        regression = coordinator._plan_regression_errors(
            corrected,
            changed_after_authorization,
        )
        self.assertTrue(any("changed the authorized task_depth" in error for error in regression))
        self.assertTrue(
            any(
                "task_depth must exactly match" in error
                for error in coordinator._validate_plan(
                    changed_after_authorization,
                    task,
                    brief,
                )
            )
        )

    def test_sparse_wood_cabin_requires_full_cards_and_authorization_before_execution(self) -> None:
        self.assertIn("scene_task_eligibility", SUPERVISOR_SCHEMA["required"])
        self.assertEqual(
            ["eligible", "not_applicable", "unclear"],
            SUPERVISOR_SCHEMA["properties"]["scene_task_eligibility"][
                "properties"
            ]["decision"]["enum"],
        )
        project_fields = {
            "user_facts",
            "reference_observations",
            "codex_assumptions",
            "verified_scene_facts",
            "hard_constraints",
            "acceptance",
        }
        self.assertTrue(project_fields.issubset(PLANNING_SCHEMA["required"]))
        expected_stage_fields = {
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
        }
        self.assertEqual(expected_stage_fields, set(STAGE_CARD_SCHEMA["required"]))
        step_required = set(
            STAGE_CARD_SCHEMA["properties"]["ordered_construction_steps"]["items"][
                "required"
            ]
        )
        self.assertIn("failure_minimum_repair", step_required)

        generic_planning = _vague_plan()
        authorized = _plan(two_stages=True)
        encoded_authorization = json.dumps(authorized, ensure_ascii=False)
        self.assertGreater(len(encoded_authorization), 10_000)
        self.assertGreater(
            sum("\u4e00" <= character <= "\u9fff" for character in encoded_authorization),
            4_000,
        )
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            image = Path(directory) / "wood-cabin.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            client = _ScriptedClient(
                planning=authorized,
                planning_sequence=[generic_planning, authorized],
                authorization=authorized,
                authorization_sequence=[_vague_plan(), _vague_plan(), authorized],
                execution=_execution([str(image)]),
            )
            harness = _ProjectHarness(client)
            harness.start("建一个木屋")
            project = harness.wait_terminal()

        self.assertEqual("completed", project["status"])
        self.assertEqual(
            ["active", "complete"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )
        starts = [
            (client.roles[params["threadId"]], params)
            for method, params in client.requests
            if method == "turn/start"
        ]
        authorization_index = next(
            index
            for index, (role, params) in enumerate(starts)
            if role == "supervisor"
            and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        )
        execution_indexes = [
            index for index, (role, _params) in enumerate(starts) if role == "execution"
        ]
        authorization_indexes = [
            index
            for index, (role, params) in enumerate(starts)
            if role == "supervisor"
            and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        ]
        self.assertEqual(3, len(authorization_indexes))
        self.assertLess(max(authorization_indexes), min(execution_indexes))
        first_execution_prompt = starts[execution_indexes[0]][1]["input"][0]["text"]
        self.assertIn("ordered_construction_steps", first_execution_prompt)
        self.assertIn("搭建木屋承重壳体与楼板基础", first_execution_prompt)
        self.assertNotIn("建立木屋墙板洞口与防雨外壳", first_execution_prompt)
        self.assertNotIn("完善细节", first_execution_prompt)
        authorization_prompt = starts[authorization_index][1]["input"][0]["text"]
        self.assertNotIn("完善细节", authorization_prompt)
        planning_repairs = [
            params["input"][0]["text"]
            for role, params in starts
            if role == "planning"
            and "cannot be submitted to Supervisor yet" in params["input"][0]["text"]
        ]
        self.assertEqual(1, len(planning_repairs))
        self.assertIn("完善细节", planning_repairs[0])
        self.assertEqual(
            STAGE_CARD_SCHEMA,
            SUPERVISOR_PLAN_SCHEMA["properties"]["stages"]["items"],
        )

    def test_handoff_accepts_large_blueprint_but_remains_bounded(self) -> None:
        self.assertEqual("木" * 200_000, handoff("木" * 200_000))
        with self.assertRaises(BridgeError) as raised:
            handoff("木" * 400_000)
        self.assertEqual("PROJECT_THREAD_PAYLOAD_TOO_LARGE", raised.exception.code)

    def test_ultra_subagents_are_read_only_capacity_not_second_writers(self) -> None:
        execution = role_instructions("execution")
        reviewer = role_instructions("visual_review")
        self.assertIn("Do not proactively spawn internal subagents", execution)
        self.assertIn("cannot independently prove a child", execution)
        self.assertIn("All writes and their evidence acquisition stay serial", execution)
        self.assertIn("matching native collaboration events", reviewer)
        self.assertIn("dispatch suitable independent read-only", reviewer)
        self.assertIn("same-quality work serially", reviewer)
        self.assertIn("strictly read-only", reviewer)
        self.assertIn("Bridge coordinator exclusively owns the native Goal", reviewer)
        self.assertIn("never call create_goal, get_goal, or update_goal", execution)
        self.assertNotIn("Do not create subagents", execution)

    def test_every_stage_loop_prompt_carries_user_reference_and_acceptance(self) -> None:
        supervisor = _plan()
        supervisor["user_facts"].append(
            "User fact: 左侧必须保留悬挑红色玻璃三角窗，禁止盒状替代。"
        )
        supervisor["reference_observations"].append(
            "Reference observation: 参考图显示非对称屋顶与左侧悬挑三角窗。"
        )
        supervisor["acceptance"].append(
            "实际图像必须清楚显示红色玻璃三角窗、非对称屋顶且不是 Box-heavy 拼装。"
        )
        stage = supervisor["stages"][0]
        execution = _execution()
        visual = _review("visual")
        technical = _review("technical")
        decision = {
            "decision": "repair",
            "summary": "修复主形体。",
            "repair_card": "只修复三角窗与非对称屋顶。",
            "collaboration_mode": "serial-fallback",
        }
        prompts = (
            stage_prompt(supervisor, stage),
            review_prompt(
                "visual", supervisor, stage, execution, has_images=False
            ),
            blocked_prompt(
                supervisor,
                stage,
                _execution(outcome="blocked"),
            ),
            decision_prompt(
                supervisor, stage, execution, visual, technical
            ),
            repair_prompt(supervisor, stage, execution, decision),
        )
        for prompt in prompts:
            self.assertIn("左侧必须保留悬挑红色玻璃三角窗", prompt)
            self.assertIn("参考图显示非对称屋顶", prompt)
            self.assertIn("实际图像必须清楚显示红色玻璃三角窗", prompt)
            self.assertIn("GLOBAL CONSTRAINT CAPSULE", prompt)
        for prompt in (
            stage_prompt(supervisor, stage),
            review_prompt(
                "visual", supervisor, stage, execution, has_images=True
            ),
            decision_prompt(
                supervisor, stage, execution, visual, technical
            ),
            plan_authorization_prompt(_brief(), supervisor),
        ):
            self.assertIn("Box-heavy", prompt)

    def test_detailed_user_task_reference_and_acceptance_cannot_be_dropped(self) -> None:
        task = (
            "在 Houdini 中建造完整木屋，左侧必须有悬挑红色玻璃三角窗，"
            "屋顶必须非对称，并且禁止 Box 或盒状替代。"
        )
        brief = _brief()
        brief["user_facts"] = ["User fact: " + task]
        brief["reference_observations"] = [
            "Reference observation: 参考图左侧三角窗突出于非对称屋檐。"
        ]
        brief["acceptance"] = [
            "实际图像必须证明红色玻璃三角窗、非对称屋顶和非盒状主形体。"
        ]
        plan = _plan()
        for field in (
            "user_facts",
            "reference_observations",
            "hard_constraints",
            "acceptance",
        ):
            plan[field] = copy.deepcopy(brief[field])
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )
        self.assertEqual([], coordinator._validate_plan(plan, task, brief))

        dropped = copy.deepcopy(plan)
        dropped["reference_observations"] = []
        dropped["acceptance"] = ["木屋看起来完整。"]
        dropped["user_facts"] = ["User fact: 用户要求建一个木屋。"]
        errors = coordinator._validate_plan(dropped, task, brief)
        self.assertTrue(any("dropped reference_observations" in e for e in errors))
        self.assertTrue(any("dropped acceptance" in e for e in errors))
        self.assertTrue(any("complete original user task" in e for e in errors))

        bound = coordinator._brief_with_verbatim_task(_brief(), task)
        self.assertTrue(any(task in value for value in bound["user_facts"]))

    def test_supervisor_cannot_drop_planning_only_reference_findings(self) -> None:
        task = "在 Houdini 中建造完整木屋。"
        planning = _plan()
        planning["reference_observations"].append(
            "Reference observation: 参考图左侧有悬挑红色玻璃三角窗，屋顶为非对称折面。"
        )
        planning["acceptance"].append(
            "实际透视图必须证明红色三角窗悬挑、非对称屋顶折面和对应结构支撑。"
        )
        authorization = _plan()
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )

        errors = coordinator._validate_plan(authorization, task, planning)
        self.assertTrue(any("dropped reference_observations" in e for e in errors))
        self.assertTrue(any("dropped acceptance" in e for e in errors))
        self.assertTrue(
            coordinator._plan_regression_errors(planning, authorization)
        )

    def test_bridge_collaboration_metadata_does_not_fake_blueprint_regression(self) -> None:
        planning = _plan()
        authorization = copy.deepcopy(planning)
        planning["collaboration_mode"] = "used-with-real-events"
        authorization["collaboration_mode"] = "serial-fallback"

        self.assertEqual(
            [],
            ProjectThreadCoordinator._plan_regression_errors(
                planning, authorization
            ),
        )

    def test_complete_user_task_preservation_is_unicode_and_single_fact_safe(self) -> None:
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )
        tasks = (
            "ابنِ كوخًا، والنافذة الحمراء إلزامية.",
            "非対称の屋根と赤い三角窓を必ず作る。",
            "반드시 비대칭 지붕과 빨간 삼각형 창문을 만든다.",
            "🏠必须保留🔺红窗，禁止📦。",
        )
        for task in tasks:
            with self.subTest(task=task):
                source = _brief()
                source["user_facts"] = ["User task verbatim:\n" + task]
                preserved = _plan()
                preserved["user_facts"] = ["User fact: " + task]
                preserved_errors = coordinator._validate_plan(
                    preserved, task, source
                )
                self.assertFalse(
                    any(
                        "complete original user task" in error
                        for error in preserved_errors
                    )
                )

                dropped = copy.deepcopy(preserved)
                dropped["user_facts"] = ["User fact: 建一个木屋。"]
                errors = coordinator._validate_plan(dropped, task, source)
                self.assertTrue(
                    any("complete original user task" in error for error in errors)
                )

        split_task = "红色三角窗禁止盒状替代"
        source = _brief()
        source["user_facts"] = ["User task verbatim:\n" + split_task]
        split = _plan()
        split["user_facts"] = ["红色三角窗", "禁止盒状替代"]
        errors = coordinator._validate_plan(split, split_task, source)
        self.assertTrue(any("complete original user task" in error for error in errors))

    def test_tool_projection_is_bounded_keeps_failures_and_redacts_secrets(self) -> None:
        pending = _PendingTurn("project-one", "execution", "thread-execution")
        pending.add_tool_evidence(
            {
                "id": "tool-failed",
                "type": "mcpToolCall",
                "tool": "hia_node_help",
                "status": "failed",
                "arguments": {
                    "path": "/obj/CABIN_BUILD",
                    "authorization": "Bearer never-expose",
                    "script": "hou.node('/obj').createNode('geo')\n" * 5_000,
                },
                "error": {
                    "code": "HIA_MODULE_UNAVAILABLE",
                    "message": "Houdini module unavailable; Bearer embedded-message-secret",
                    "stdout": "measured clearance=0.08m; api_key=embedded-stdout-secret",
                    "raw_observation": "door support path /obj/CABIN_BUILD",
                    "image_base64": "sensitive-binary" * 10_000,
                },
            }
        )
        record = pending.tool_evidence["tool-failed"]
        encoded = json.dumps(record, ensure_ascii=False)
        self.assertFalse(record["ok"])
        self.assertIn("HIA_MODULE_UNAVAILABLE", encoded)
        self.assertIn("measured clearance=0.08m", encoded)
        self.assertIn("raw_observation", encoded)
        self.assertNotIn("never-expose", encoded)
        self.assertNotIn("sensitive-binary", encoded)
        self.assertNotIn("embedded-message-secret", encoded)
        self.assertNotIn("embedded-stdout-secret", encoded)
        self.assertIn("<redacted>", encoded)
        self.assertIn("<binary-omitted>", encoded)
        self.assertTrue(record["projection"]["truncated"])
        self.assertLess(len(encoded.encode("utf-8")), 65_536)

        huge = {
            "ok": True,
            "result": {
                "path": "/obj/CABIN_BUILD/OUT_STRUCTURE",
                "frame": 1,
                "errors": [{"code": "HIA_WARNING", "message": "bounded warning"}],
                "warnings": ["support clearance needs review"],
                "measurement": "clearance=0.08m",
                "validation": {"status": "verified"},
                "created_or_changed_paths": ["/obj/CABIN_BUILD/OUT_STRUCTURE"],
                "bulk": [
                    {f"nested_{index}": "木屋测量输出" * 2_000}
                    for index in range(200)
                ],
            },
        }
        projected, truncated = project_tool_evidence(huge)
        projected_text = json.dumps(projected, ensure_ascii=False)
        self.assertTrue(truncated)
        self.assertLess(len(projected_text.encode("utf-8")), 65_536)
        for expected in (
            "/obj/CABIN_BUILD/OUT_STRUCTURE",
            "clearance=0.08m",
            "HIA_WARNING",
            "support clearance needs review",
            "verified",
        ):
            self.assertIn(expected, projected_text)
        for index in range(80):
            pending.add_tool_evidence(
                {
                    "id": f"tool-large-{index}",
                    "type": "mcpToolCall",
                    "tool": "hia_inspect",
                    "status": "completed",
                    "arguments": {"path": f"/obj/CABIN_BUILD/PART_{index}"},
                    "result": {"structuredContent": huge},
                }
            )
        self.assertLessEqual(pending.tool_evidence_bytes, 131_072)
        self.assertTrue(pending.tools_truncated)
        self.assertLess(len(pending.tool_evidence), 64)

    def test_full_plan_information_floor_is_not_a_length_only_fixture_check(self) -> None:
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            _ScriptedClient(),
            EventBuffer(),
        )
        qualified = _plan()
        self.assertGreaterEqual(coordinator._information_units(qualified), 10_000)
        self.assertEqual([], coordinator._validate_plan(qualified, "建一个木屋", _brief()))
        short_schema_complete = copy.deepcopy(qualified)
        for stage in short_schema_complete["stages"]:
            stage["objective"] = "为木屋建立可编辑结构并检查实际证据。"
            for field in (
                "prerequisites",
                "inputs",
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
            ):
                stage[field] = ["木屋按需完善细节并检查质量。"]
            for step in stage["ordered_construction_steps"]:
                for key in step:
                    step[key] = "在 /obj/CABIN 中按需优化木屋并连接输入，参数依赖后检查证据。"
        errors = coordinator._validate_plan(
            short_schema_complete, "建一个木屋", _brief()
        )
        self.assertTrue(any("10000" in error for error in errors))
        self.assertTrue(any("repeats a mechanical template" in error for error in errors))


class ProjectWorkflowBehaviorTests(unittest.TestCase):
    def test_native_goal_activates_only_after_exact_root_turn_is_attached(self) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        specification = harness.coordinator.prepare_project(
            "thread-root", "建一个木屋"
        )

        self.assertFalse(any(
            method == "thread/goal/set" for method, _params in client.requests
        ))
        client.complete_root("turn-root")
        harness.coordinator.attach_root_turn(
            specification["project_id"], "turn-root"
        )

        self.assertEqual("active", client.goals["thread-root"]["status"])
        harness.coordinator.interrupt_project()

    def test_unowned_native_goal_continuation_is_hidden_and_interrupted(self) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        project_id = harness.start("建一个木屋")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "planning"
                for method, params in client.requests
            ):
                break
            time.sleep(0.01)

        consumed = harness.coordinator.handle_client_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": "thread-root",
                    "turn": {"id": "goal-auto-turn", "status": "inProgress"},
                },
            },
            selected_thread_id="thread-root",
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            interrupted = any(
                method == "turn/interrupt"
                and params == {
                    "threadId": "thread-root",
                    "turnId": "goal-auto-turn",
                }
                for method, params in client.requests
            )
            if interrupted:
                break
            time.sleep(0.01)

        self.assertTrue(consumed)
        self.assertTrue(any(
            method == "turn/interrupt"
            and params == {
                "threadId": "thread-root",
                "turnId": "goal-auto-turn",
            }
            for method, params in client.requests
        ))
        self.assertEqual("active", client.goals["thread-root"]["status"])
        self.assertIn(
            ("thread-root", "goal-auto-turn"),
            harness.coordinator._suppressed_root_turns,
        )
        harness.coordinator.interrupt_project()

    def test_transfer_waits_only_for_target_and_accepts_completed_pending_race(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            client,
            EventBuffer(),
            turn_timeout_seconds=1.0,
        )
        specification = coordinator.prepare_project("thread-root", "build a cabin")
        project_id = specification["project_id"]
        snapshot = coordinator.snapshot(
            mode="team",
            writable=True,
            settings_state_status="memory",
        )["projects"][0]
        by_role = {record["role"]: record["thread_id"] for record in snapshot["threads"]}

        with coordinator._condition:
            coordinator._pending["thread-root"].completed = True
            active_other = _PendingTurn(
                project_id,
                "execution",
                by_role["execution"],
            )
            coordinator._pending[by_role["execution"]] = active_other
        self.assertTrue(coordinator.begin_thread_transfer(by_role["planning"]))
        coordinator.finish_thread_transfer(by_role["planning"])

        with coordinator._condition:
            coordinator._pending.pop(by_role["execution"], None)
            completed_target = _PendingTurn(
                project_id,
                "planning",
                by_role["planning"],
            )
            completed_target.completed = True
            coordinator._pending[by_role["planning"]] = completed_target
        self.assertTrue(coordinator.begin_thread_transfer(by_role["planning"]))
        metadata = coordinator.replace_thread_id(
            by_role["planning"],
            "thread-planning-forked",
        )
        self.assertEqual(
            {"project_id": project_id, "role": "planning"},
            metadata,
        )
        coordinator.finish_thread_transfer(
            by_role["planning"],
            "thread-planning-forked",
        )

        with coordinator._condition:
            coordinator._pending.clear()
        coordinator.interrupt_project()

    def _junction(self, link: Path, target: Path) -> None:
        if os.name != "nt":
            self.skipTest("Windows junction behavior")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not link.exists():
            self.skipTest("Creating a test junction is not permitted")

    def _valid_image(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=runtime_tmp)
        image = Path(directory.name) / "review.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        return directory, image

    @staticmethod
    def _bind_execution(execution: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(execution))
        pending = _PendingTurn("project-one", "execution", "thread-execution")
        for evidence in value["technical_evidence"]:
            match = re.search(r"hia_[a-z0-9_]+", evidence["source"])
            pending.add_tool_evidence(
                {
                    "id": evidence["tool_item_id"],
                    "type": "mcpToolCall",
                    "tool": match.group(0) if match else "hia_inspect",
                    "status": "completed",
                    "arguments": {
                        "path": evidence["scope_or_path"],
                        "frame": 1,
                    },
                    "result": {
                        "structuredContent": {
                            "ok": True,
                            "result": {
                                "path": evidence["scope_or_path"],
                                "frame": 1,
                                "observation": evidence[
                                    "observation_or_measurement"
                                ],
                                "validation": {"status": evidence["result"]},
                            },
                        }
                    },
                }
            )
        value["_bridge_evidence"] = {
            "tools": copy.deepcopy(pending.tool_evidence),
            "tools_truncated": pending.tools_truncated,
        }
        return value

    @staticmethod
    def _bind_sequence_capture(
        execution: Mapping[str, Any],
        *,
        item_id: str,
        endpoints: list[Mapping[str, Any]],
        ok: bool,
        delivery_status: str = "returned",
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(execution))
        pending = _PendingTurn("project-one", "execution", "thread-execution")
        pending.add_tool_evidence(
            {
                "id": item_id,
                "type": "mcpToolCall",
                "tool": "hia_capture_viewport",
                "status": "completed",
                "arguments": {
                    "frame_range": [1, 48],
                    "capture_mode": "flipbook",
                    "return_image": True,
                },
                "result": {
                    "structuredContent": {
                        "ok": ok,
                        "result": {
                            "mode": "sequence",
                            "storage_scope": "runtime_fallback",
                            "source_hip_path": None,
                            "image_delivery": {"status": delivery_status},
                            "sequence": {
                                "endpoints": [dict(endpoint) for endpoint in endpoints],
                                # Legacy arrays are intentionally not an image
                                # identity fallback for project review.
                                "evidence_paths": [
                                    endpoint["evidence_path"]
                                    for endpoint in endpoints
                                    if isinstance(endpoint.get("evidence_path"), str)
                                ],
                                "evidence_frames": [
                                    endpoint["requested_frame"]
                                    for endpoint in endpoints
                                    if isinstance(endpoint.get("evidence_path"), str)
                                ],
                            },
                        },
                    }
                },
            }
        )
        value.setdefault("_bridge_evidence", {}).setdefault("tools", {}).update(
            copy.deepcopy(pending.tool_evidence)
        )
        return value

    def test_technical_evidence_is_related_to_real_projection_not_tool_name(self) -> None:
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
        )
        valid = self._bind_execution(_execution([]))
        self.assertEqual((), coordinator._review_images(valid))
        prompt = review_prompt(
            "technical", _plan(), _stage("搭建可编辑木屋壳体", "/obj/CABIN_BUILD"), valid,
            has_images=False,
        )
        self.assertIn("_bridge_evidence", prompt)
        self.assertIn("/obj/CABIN_BUILD/OUT_STRUCTURE", prompt)
        self.assertIn("最小净空 0.08m", prompt)

        wrong_scope = copy.deepcopy(valid)
        wrong_scope["technical_evidence"][0]["scope_or_path"] = (
            "/obj/UNRELATED_BUILD/OUT_STRUCTURE"
        )
        with self.assertRaises(BridgeError) as scope_error:
            coordinator._review_images(wrong_scope)
        self.assertEqual(
            "PROJECT_TECHNICAL_EVIDENCE_UNRELATED", scope_error.exception.code
        )

        forged_measurement = copy.deepcopy(valid)
        forged_measurement["technical_evidence"][1][
            "observation_or_measurement"
        ] = "屋顶支撑接触，但模型声称最小净空 9.99m。"
        with self.assertRaises(BridgeError) as measurement_error:
            coordinator._review_images(forged_measurement)
        self.assertEqual(
            "PROJECT_TECHNICAL_EVIDENCE_UNRELATED",
            measurement_error.exception.code,
        )

    def test_review_pass_requires_specific_distinct_claim_observations(self) -> None:
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
        )
        stage = _stage("搭建可编辑木屋壳体", "/obj/CABIN_BUILD")
        execution = _execution([str(REPOSITORY_ROOT / ".runtime" / "image.png")])
        coordinator._validate_review("visual", stage, _review("visual"), execution)
        coordinator._validate_review("technical", stage, _review("technical"), execution)

        repeated = _review("visual")
        for claim in repeated["claims"]:
            claim["actual_evidence"] = ["looks good"]
        with self.assertRaises(BridgeError) as generic_error:
            coordinator._validate_review("visual", stage, repeated, execution)
        self.assertEqual(
            "PROJECT_REVIEW_CLAIM_EVIDENCE_UNRELATED", generic_error.exception.code
        )

        unrelated = _review("technical")
        unrelated["claims"][1]["evidence_refs"] = ["tech-output-1"]
        with self.assertRaises(BridgeError) as unrelated_error:
            coordinator._validate_review("technical", stage, unrelated, execution)
        self.assertEqual(
            "PROJECT_REVIEW_CLAIM_EVIDENCE_UNRELATED",
            unrelated_error.exception.code,
        )

    def test_external_block_requires_current_real_failed_hia_item(self) -> None:
        pending = _PendingTurn("project-one", "execution", "thread-execution")
        pending.add_tool_evidence(
            {
                "id": "tool-current-failure",
                "type": "mcpToolCall",
                "tool": "hia_node_help",
                "status": "error",
                "error": {
                    "code": "HIA_SESSION_UNAVAILABLE",
                    "message": "Houdini service unavailable and session unreachable",
                },
            }
        )
        execution = _execution(outcome="blocked")
        execution["_bridge_evidence"] = {"tools": pending.tool_evidence}
        decision = {
            "decision": "blocked",
            "external_dependency": {
                "proven": True,
                "required_external_change": "用户必须恢复当前 Houdini service 后才能继续。",
                "observed_blocker": "HIA_SESSION_UNAVAILABLE 表明 Houdini service unavailable。",
                "why_codex_cannot_resolve": "当前 session unreachable，Codex 无法在进程外恢复 service。",
                "evidence_refs": ["tool-current-failure"],
            },
        }
        self.assertTrue(
            ProjectThreadCoordinator._proven_external_dependency(decision, execution)
        )
        claimed_only = copy.deepcopy(decision)
        claimed_only["external_dependency"]["evidence_refs"] = ["tech-output-1"]
        self.assertFalse(
            ProjectThreadCoordinator._proven_external_dependency(
                claimed_only, execution
            )
        )
        old_turn = copy.deepcopy(decision)
        old_turn["external_dependency"]["evidence_refs"] = ["tool-old-turn"]
        self.assertFalse(
            ProjectThreadCoordinator._proven_external_dependency(old_turn, execution)
        )

    def test_runtime_and_hip_capture_reparse_chains_are_rejected(self) -> None:
        base = REPOSITORY_ROOT / ".runtime" / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            outside_runtime = root / "outside-runtime"
            outside_runtime.mkdir()
            runtime_image = outside_runtime / "runtime.png"
            runtime_image.write_bytes(b"\x89PNG\r\n\x1a\n")
            runtime_link = root / "project" / ".runtime" / "cache" / "screenshots"
            runtime_link.parent.mkdir(parents=True)
            self._junction(runtime_link, outside_runtime)
            coordinator = ProjectThreadCoordinator(
                root / "project", _ScriptedClient(), EventBuffer()
            )
            runtime_execution = self._bind_execution(_execution([]))
            runtime_execution["review_images"] = [
                {
                    "id": "image-runtime",
                    "capture_tool_item_id": "capture-runtime",
                    "path": str(runtime_link / "runtime.png"),
                    "frame_or_time": "frame 1",
                }
            ]
            runtime_execution["_bridge_evidence"]["tools"]["capture-runtime"] = {
                "item_id": "capture-runtime",
                "tool": "hia_capture_viewport",
                "status": "completed",
                "ok": True,
                "projection": {},
                "capture": {
                    "absolute_path": str(runtime_link / "runtime.png"),
                    "storage_scope": "runtime_fallback",
                    "source_hip_path": None,
                    "actual_frame": 1,
                },
            }
            with self.assertRaises(BridgeError) as runtime_error:
                coordinator._review_images(runtime_execution)
            self.assertEqual(
                "PROJECT_REVIEW_IMAGE_SYMLINK_REJECTED",
                runtime_error.exception.code,
            )

            scene_parent = root / "scene-parent"
            scene_parent.mkdir()
            hip = scene_parent / "scene.hip"
            hip.write_bytes(b"hip")
            outside_hip = root / "outside-hip"
            outside_hip.mkdir()
            hip_image = outside_hip / "hip.png"
            hip_image.write_bytes(b"\x89PNG\r\n\x1a\n")
            hip_link = scene_parent / ".hia" / "screenshots"
            hip_link.parent.mkdir()
            self._junction(hip_link, outside_hip)
            hip_execution = self._bind_execution(_execution([]))
            hip_execution["review_images"] = [
                {
                    "id": "image-hip",
                    "capture_tool_item_id": "capture-hip",
                    "path": str(hip_link / "hip.png"),
                    "frame_or_time": "frame 1",
                }
            ]
            hip_execution["_bridge_evidence"]["tools"]["capture-hip"] = {
                "item_id": "capture-hip",
                "tool": "hia_capture_viewport",
                "status": "completed",
                "ok": True,
                "projection": {},
                "capture": {
                    "absolute_path": str(hip_link / "hip.png"),
                    "storage_scope": "hip",
                    "source_hip_path": str(hip),
                    "actual_frame": 1,
                },
            }
            with self.assertRaises(BridgeError) as hip_error:
                coordinator._review_images(hip_execution)
            self.assertEqual(
                "PROJECT_REVIEW_IMAGE_SYMLINK_REJECTED", hip_error.exception.code
            )

    def test_verified_image_is_forwarded_as_real_local_image(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        client = _ScriptedClient(execution=_execution([str(image)]))
        harness = _ProjectHarness(client)
        harness.start()
        self.assertEqual("completed", harness.wait_terminal()["status"])

        visual = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "visual_review"
        )
        self.assertIn(
            {"type": "localImage", "path": str(image.resolve())},
            visual["input"],
        )

    def test_sequence_first_and_last_endpoints_bind_exact_paths_and_frames(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            first, last = Path(directory) / "first.png", Path(directory) / "last.png"
            first.write_bytes(b"\x89PNG\r\n\x1a\n")
            last.write_bytes(b"\x89PNG\r\n\x1a\n")
            execution = self._bind_execution(_execution([]))
            execution["review_images"] = [
                {
                    "id": "sequence-first",
                    "capture_tool_item_id": "capture-sequence",
                    "path": str(first),
                    "frame_or_time": "frame 1",
                },
                {
                    "id": "sequence-last",
                    "capture_tool_item_id": "capture-sequence",
                    "path": str(last),
                    "frame_or_time": "frame 48",
                },
            ]
            execution = self._bind_sequence_capture(
                execution,
                item_id="capture-sequence",
                ok=True,
                delivery_status="path_only",
                endpoints=[
                    {
                        "endpoint": "first",
                        "requested_frame": 1.0,
                        "status": "captured",
                        "actual_frame": 1.0,
                        "cook_frame": 1.0,
                        "quality_status": "passed",
                        "evidence_path": str(first),
                    },
                    {
                        "endpoint": "last",
                        "requested_frame": 48.0,
                        "status": "captured",
                        "actual_frame": 48.0,
                        "cook_frame": 48.0,
                        "quality_status": "passed",
                        "evidence_path": str(last),
                    },
                ],
            )
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
            )
            self.assertEqual(
                (str(first.resolve()), str(last.resolve())),
                coordinator._review_images(execution),
            )

    def test_sequence_failed_endpoint_cannot_borrow_other_or_legacy_path(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            first, last = Path(directory) / "first.png", Path(directory) / "last.png"
            first.write_bytes(b"\x89PNG\r\n\x1a\n")
            last.write_bytes(b"\x89PNG\r\n\x1a\n")
            base = self._bind_execution(_execution([]))
            base["review_images"] = [
                {
                    "id": "failed-first",
                    "capture_tool_item_id": "capture-partial",
                    "path": str(last),
                    "frame_or_time": "frame 1",
                }
            ]
            partial = self._bind_sequence_capture(
                base,
                item_id="capture-partial",
                ok=False,
                endpoints=[
                    {
                        "endpoint": "first",
                        "requested_frame": 1.0,
                        "status": "failed",
                        "error": {"code": "CAPTURE_FAILED"},
                    },
                    {
                        "endpoint": "last",
                        "requested_frame": 48.0,
                        "status": "captured",
                        "actual_frame": 48.0,
                        "cook_frame": 48.0,
                        "quality_status": "passed",
                        "evidence_path": str(last),
                    },
                ],
            )
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
            )
            with self.assertRaises(BridgeError) as wrong_endpoint:
                coordinator._review_images(partial)
            self.assertEqual("PROJECT_REVIEW_IMAGE_UNBOUND", wrong_endpoint.exception.code)

            both_failed = copy.deepcopy(partial)
            both_failed["_bridge_evidence"]["tools"]["capture-partial"]["capture"][
                "endpoints"
            ] = [
                {"endpoint": "first", "requested_frame": 1.0, "status": "failed"},
                {"endpoint": "last", "requested_frame": 48.0, "status": "failed"},
            ]
            with self.assertRaises(BridgeError) as no_endpoint:
                coordinator._review_images(both_failed)
            self.assertEqual("PROJECT_REVIEW_IMAGE_UNBOUND", no_endpoint.exception.code)

            legacy_only = copy.deepcopy(partial)
            capture = legacy_only["_bridge_evidence"]["tools"]["capture-partial"][
                "capture"
            ]
            capture.pop("endpoints")
            capture["evidence_paths"] = [str(first), str(last)]
            capture["evidence_frames"] = [1.0, 48.0]
            with self.assertRaises(BridgeError) as legacy_error:
                coordinator._review_images(legacy_only)
            self.assertEqual("PROJECT_REVIEW_IMAGE_UNBOUND", legacy_error.exception.code)

    def test_saved_hip_sibling_capture_is_valid_real_review_evidence(self) -> None:
        base = REPOSITORY_ROOT / ".runtime" / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            scene_parent = Path(directory)
            hip = scene_parent / "scene.hip"
            hip.write_bytes(b"hip")
            screenshots = scene_parent / ".hia" / "screenshots"
            screenshots.mkdir(parents=True)
            image = screenshots / "review.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            execution = self._bind_execution(_execution([]))
            execution["review_images"] = [
                {
                    "id": "image-hip",
                    "capture_tool_item_id": "capture-hip",
                    "path": str(image),
                    "frame_or_time": "frame 1",
                }
            ]
            execution["_bridge_evidence"]["tools"]["capture-hip"] = {
                "item_id": "capture-hip",
                "tool": "hia_capture_viewport",
                "status": "completed",
                "ok": True,
                "projection": {},
                "capture": {
                    "absolute_path": str(image),
                    "storage_scope": "hip",
                    "source_hip_path": str(hip),
                    "actual_frame": 1,
                },
            }
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
            )
            self.assertEqual(
                (str(image.resolve()),), coordinator._review_images(execution)
            )
            failed_capture = copy.deepcopy(execution)
            failed_capture["_bridge_evidence"]["tools"]["capture-hip"]["ok"] = False
            with self.assertRaises(BridgeError) as failed_error:
                coordinator._review_images(failed_capture)
            self.assertEqual(
                "PROJECT_REVIEW_IMAGE_UNBOUND", failed_error.exception.code
            )

    def test_visual_review_without_image_is_repaired_without_blocking_goal(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        repair_decision = {
            "decision": "repair",
            "summary": "必须补充当前阶段真实图像。",
            "repair_card": "只获取本阶段新 viewport capture 并重新审查。",
            "collaboration_mode": "serial-fallback",
        }
        client = _ScriptedClient(
            execution=_execution([str(image)], evidence_suffix="-repair"),
            execution_sequence=[
                _execution([]),
                _execution([str(image)], evidence_suffix="-repair"),
            ],
            visual_sequence=[_generic_review("visual"), _repair_review("visual")],
            technical_sequence=[_review("technical")],
            technical=_review("technical", "-repair"),
            visual=_review("visual", "-repair"),
            decision_sequence=[repair_decision],
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()

        self.assertEqual("completed", project["status"])
        self.assertEqual(
            ["active", "complete"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )
        self.assertGreaterEqual(
            sum(
                method == "turn/start"
                and client.roles[params["threadId"]] == "visual_review"
                for method, params in client.requests
            ),
            3,
        )

    def test_executor_blocked_runs_both_reviews_before_terminal(self) -> None:
        client = _ScriptedClient(
            execution=_execution(outcome="blocked"),
            visual=_repair_review("visual"),
            technical=_repair_review("technical"),
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()

        self.assertEqual("blocked", project["status"])
        self.assertEqual(
            ["active", "blocked"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )
        roles = [
            client.roles[params["threadId"]]
            for method, params in client.requests
            if method == "turn/start"
        ]
        self.assertEqual(1, roles.count("execution"))
        self.assertEqual(1, roles.count("visual_review"))
        self.assertEqual(1, roles.count("technical_review"))
        supervisor_prompts = [
            params["input"][0]["text"]
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "supervisor"
        ]
        self.assertTrue(any("Executor reported blocked" in text for text in supervisor_prompts))

    def test_visual_guidance_after_blocked_decision_reruns_only_visual_review(
        self,
    ) -> None:
        class _BlockedReviewRaceClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(
                    execution=_execution(outcome="blocked"),
                    visual_sequence=[
                        _repair_review("visual"),
                        _repair_review("visual"),
                    ],
                    technical=_repair_review("technical"),
                )
                self.inject_guidance: Any = None
                self.blocked_decisions = 0

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if (
                    method == "turn/start"
                    and params.get("outputSchema") == SUPERVISOR_BLOCKED_SCHEMA
                ):
                    self.blocked_decisions += 1
                    if self.blocked_decisions == 1 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return result

        client = _BlockedReviewRaceClient()
        harness = _ProjectHarness(client)
        guidance = "Recheck the blocker view for the exact missing camera evidence."

        def inject() -> None:
            project = harness.snapshot()["projects"][0]
            visual_thread = next(
                item
                for item in project["threads"]
                if item["role"] == "visual_review"
            )
            receipt = harness.coordinator.append_guidance(
                project["project_id"],
                visual_thread["thread_id"],
                guidance,
            )
            self.assertTrue(receipt["guidance_accepted"])

        client.inject_guidance = inject
        harness.start()
        project = harness.wait_terminal()

        self.assertEqual("blocked", project["status"])
        starts = [
            (client.roles[params["threadId"]], params)
            for method, params in client.requests
            if method == "turn/start"
        ]
        self.assertEqual(1, sum(role == "execution" for role, _params in starts))
        self.assertEqual(2, sum(role == "visual_review" for role, _params in starts))
        self.assertEqual(1, sum(role == "technical_review" for role, _params in starts))
        self.assertEqual(2, client.blocked_decisions)
        visual_prompts = [
            params["input"][0]["text"]
            for role, params in starts
            if role == "visual_review"
        ]
        self.assertIn(guidance, visual_prompts[-1])

    def test_technical_guidance_during_blocked_goal_rpc_reruns_only_technical_review(
        self,
    ) -> None:
        class _BlockedGoalRaceClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(
                    execution=_execution(outcome="blocked"),
                    visual=_repair_review("visual"),
                    technical_sequence=[
                        _repair_review("technical"),
                        _repair_review("technical"),
                    ],
                )
                self.inject_guidance: Any = None
                self.blocked_goal_calls = 0

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if method == "thread/goal/set" and params.get("status") == "blocked":
                    self.blocked_goal_calls += 1
                    if self.blocked_goal_calls == 1 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return result

        client = _BlockedGoalRaceClient()
        harness = _ProjectHarness(client)
        guidance = "Recheck the failed HIA session evidence and exact blocker code."

        def inject() -> None:
            project = harness.snapshot()["projects"][0]
            technical_thread = next(
                item
                for item in project["threads"]
                if item["role"] == "technical_review"
            )
            harness.coordinator.append_guidance(
                project["project_id"],
                technical_thread["thread_id"],
                guidance,
            )

        client.inject_guidance = inject
        harness.start()
        project = harness.wait_terminal()

        self.assertEqual("blocked", project["status"])
        starts = [
            (client.roles[params["threadId"]], params)
            for method, params in client.requests
            if method == "turn/start"
        ]
        self.assertEqual(1, sum(role == "execution" for role, _params in starts))
        self.assertEqual(1, sum(role == "visual_review" for role, _params in starts))
        self.assertEqual(2, sum(role == "technical_review" for role, _params in starts))
        technical_prompts = [
            params["input"][0]["text"]
            for role, params in starts
            if role == "technical_review"
        ]
        self.assertIn(guidance, technical_prompts[-1])
        self.assertEqual(
            ["active", "blocked", "active", "blocked"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )

    def test_unproven_executor_block_is_repaired_without_goal_block(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        unproven = {
            "decision": "blocked",
            "summary": "模型声称被阻断。",
            "repair_card": "",
            "collaboration_mode": "serial-fallback",
            "external_dependency": {
                "proven": True,
                "required_external_change": "用户需要处理未知事项。",
                "observed_blocker": "模型仅引用自己填写的 technical evidence id。",
                "why_codex_cannot_resolve": "没有当前真实失败 HIA item 支持此说法。",
                "evidence_refs": ["tech-output-1"],
            },
        }
        repair = {
            "decision": "repair",
            "summary": "继续由执行角色修正并获取真实证据。",
            "repair_card": "恢复当前阶段并获取新的 HIA 测量与 capture。",
            "collaboration_mode": "serial-fallback",
        }
        client = _ScriptedClient(
            execution=_execution([str(image)], evidence_suffix="-repair"),
            execution_sequence=[
                _execution(outcome="blocked"),
                _execution([str(image)], evidence_suffix="-repair"),
            ],
            visual=_review("visual", "-repair"),
            technical=_review("technical", "-repair"),
            visual_sequence=[_repair_review("visual")],
            technical_sequence=[_repair_review("technical")],
            blocked_decision=unproven,
            decision_sequence=[repair],
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()
        self.assertEqual("completed", project["status"])
        self.assertEqual(
            ["active", "complete"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )
        self.assertGreaterEqual(
            sum(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            ),
            2,
        )

    def test_unproven_review_block_is_repaired_then_reverified(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        repaired_image = image.with_name("review-repaired.png")
        repaired_image.write_bytes(b"\x89PNG\r\n\x1a\n")
        unsupported_block = {
            "decision": "blocked",
            "summary": "没有外部失败证据却声称阻断。",
            "repair_card": "",
            "collaboration_mode": "serial-fallback",
            "external_dependency": {
                "proven": True,
                "required_external_change": "用户需要处理未知事项。",
                "observed_blocker": "仅引用成功 inspect item，未观察到服务失败。",
                "why_codex_cannot_resolve": "没有真实不可用或错误事实。",
                "evidence_refs": ["tool-inspect-1"],
            },
        }
        repair = {
            "decision": "repair",
            "summary": "在当前阶段执行最小修复。",
            "repair_card": "仅修正当前证据覆盖并重新测量与捕获。",
            "collaboration_mode": "serial-fallback",
        }
        passed = {
            "decision": "pass",
            "summary": "新证据与两路审查均支持通过。",
            "repair_card": "",
            "collaboration_mode": "serial-fallback",
        }
        client = _ScriptedClient(
            execution=_execution([str(repaired_image)], evidence_suffix="-repair"),
            execution_sequence=[
                _execution([str(image)]),
                _execution([str(repaired_image)], evidence_suffix="-repair"),
            ],
            visual_sequence=[_review("visual")],
            technical_sequence=[_review("technical")],
            visual=_review("visual", "-repair"),
            technical=_review("technical", "-repair"),
            decision_sequence=[unsupported_block, repair, passed],
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()
        self.assertEqual("completed", project["status"])
        self.assertEqual(
            ["active", "complete"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )

    def test_internal_turn_failure_pauses_native_goal_without_false_block(self) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        project_id = harness.start()
        project = harness.wait_terminal()
        self.assertEqual("failed", project["status"])
        goal_statuses = [
            params["status"]
            for method, params in client.requests
            if method == "thread/goal/set"
        ]
        self.assertEqual("active", goal_statuses[0])
        self.assertEqual("paused", goal_statuses[-1])
        self.assertNotIn("blocked", goal_statuses)
        request_count = len(client.requests)
        thread_prefixes = (
            f"hia-project-{project_id[-8:]}",
            f"hia-project-planning-{project_id[-8:]}",
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and any(
            thread.is_alive()
            and thread.name.startswith(thread_prefixes)
            for thread in threading.enumerate()
        ):
            time.sleep(0.01)
        self.assertFalse(any(
            thread.is_alive()
            and thread.name.startswith(thread_prefixes)
            for thread in threading.enumerate()
        ))
        time.sleep(0.05)
        self.assertEqual(request_count, len(client.requests))

    def test_goal_terminal_failure_compensates_to_pause_without_false_success(self) -> None:
        class _GoalFailureClient(_ScriptedClient):
            def __init__(self, failures: set[str]) -> None:
                super().__init__()
                self.failures = set(failures)

            def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
                if method == "thread/goal/set" and params.get("status") in self.failures:
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                    raise BridgeError(
                        "CODEX_RPC_ERROR",
                        f"goal {params.get('status')} rejected",
                        http_status=502,
                    )
                return super().request(method, params)

        client = _GoalFailureClient({"complete"})
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project_id = coordinator.prepare_project("thread-root", "建一个木屋")[
            "project_id"
        ]
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        outcome = coordinator._project_terminal_transition(
            project_id,
            goal_status="complete",
            status="completed",
            stage="done",
        )
        project = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        self.assertEqual("committed", outcome)
        self.assertEqual("failed", project["status"])
        self.assertIn("安全暂停", project["stage"])
        self.assertEqual("paused", client.goals["thread-root"]["status"])
        self.assertFalse(coordinator.workflow_active())

        locked_client = _GoalFailureClient({"complete", "paused"})
        locked = ProjectThreadCoordinator(
            REPOSITORY_ROOT, locked_client, EventBuffer()
        )
        locked_id = locked.prepare_project("thread-root", "建一个木屋")["project_id"]
        locked_client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        self.assertEqual(
            "locked",
            locked._project_terminal_transition(
                locked_id,
                goal_status="complete",
                status="completed",
                stage="done",
            ),
        )
        locked_project = locked.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        self.assertEqual("running", locked_project["status"])
        self.assertIn("Goal", locked_project["stage"])
        self.assertTrue(locked.workflow_active())

    def test_terminal_guard_restores_goal_when_guidance_arrives_after_rpc(self) -> None:
        class _GuidanceRaceClient(_ScriptedClient):
            coordinator: ProjectThreadCoordinator | None = None
            project_id: str | None = None

            def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
                result = super().request(method, params)
                if (
                    method == "thread/goal/set"
                    and params.get("status") == "complete"
                    and self.coordinator is not None
                    and self.project_id is not None
                ):
                    with self.coordinator._condition:
                        project = self.coordinator._require_project(self.project_id)
                        project["_guidance_revision"] = 1
                        self.coordinator._touch(project)
                return result

        client = _GuidanceRaceClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project_id = coordinator.prepare_project("thread-root", "建一个木屋")[
            "project_id"
        ]
        client.coordinator, client.project_id = coordinator, project_id
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        outcome = coordinator._project_terminal_transition(
            project_id,
            goal_status="complete",
            status="completed",
            stage="done",
            expected_guidance_revision=0,
        )
        project = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        self.assertEqual("stale", outcome)
        self.assertEqual("running", project["status"])
        self.assertEqual("active", client.goals["thread-root"]["status"])
        self.assertEqual(
            ["complete", "active"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )

    def test_complete_and_user_stop_are_serialized_to_one_goal_terminal(self) -> None:
        class _BlockingCompleteClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__()
                self.complete_entered = threading.Event()
                self.release_complete = threading.Event()

            def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
                if method == "thread/goal/set" and params.get("status") == "complete":
                    self.complete_entered.set()
                    self.release_complete.wait(2.0)
                return super().request(method, params)

        client = _BlockingCompleteClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project_id = coordinator.prepare_project("thread-root", "建一个木屋")[
            "project_id"
        ]
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        complete_result: dict[str, Any] = {}
        stop_result: dict[str, Any] = {}
        complete = threading.Thread(
            target=lambda: complete_result.setdefault(
                "value",
                coordinator._project_terminal_transition(
                    project_id,
                    goal_status="complete",
                    status="completed",
                    stage="done",
                ),
            )
        )
        complete.start()
        self.assertTrue(client.complete_entered.wait(1.0))
        stop = threading.Thread(
            target=lambda: stop_result.setdefault(
                "value", coordinator.interrupt_project()
            )
        )
        stop.start()
        client.release_complete.set()
        complete.join(2.0)
        stop.join(2.0)
        self.assertFalse(complete.is_alive())
        self.assertFalse(stop.is_alive())
        project = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        self.assertEqual("committed", complete_result["value"])
        self.assertFalse(stop_result["value"]["interrupted"])
        self.assertEqual("completed", project["status"])
        self.assertEqual("complete", client.goals["thread-root"]["status"])

    def test_stop_winner_cannot_be_reactivated_by_late_attach_or_reassert(self) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project("thread-root", "建一个木屋")
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        stopped = coordinator.interrupt_project()
        self.assertTrue(stopped["interrupted"])
        with mock.patch.object(coordinator, "_drive_project") as driver:
            coordinator.attach_root_turn(project["project_id"], "turn-late-ack")
        self.assertEqual(0, driver.call_count)
        self.assertFalse(coordinator._activate_goal_if_running(project["project_id"]))
        self.assertEqual("paused", client.goals["thread-root"]["status"])
        self.assertEqual(
            "interrupted",
            coordinator.snapshot(
                mode="team", writable=True, settings_state_status="memory"
            )["projects"][0]["status"],
        )

    def test_terminal_failure_interrupts_all_active_project_turns(self) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project_id = coordinator.prepare_project("thread-root", "建一个木屋")[
            "project_id"
        ]
        client.set_event_sink(coordinator.handle_client_event)
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "建一个木屋",
            "status": "active",
            "tokenBudget": None,
        }
        with coordinator._condition:
            root = coordinator._pending["thread-root"]
            root.turn_id, root.status = "turn-root-active", "inProgress"
            planning = _PendingTurn(project_id, "planning", "thread-planning")
            planning.turn_id, planning.status = "turn-planning-active", "inProgress"
            coordinator._pending["thread-planning"] = planning
            client._active[("thread-root", "turn-root-active")] = "root"
            client._active[("thread-planning", "turn-planning-active")] = "planning"
        outcome = coordinator._project_terminal_transition(
            project_id,
            goal_status="paused",
            status="failed",
            stage="项目执行失败",
            error="supervisor failed",
        )
        self.assertEqual("committed", outcome)
        self.assertFalse(client._active)
        self.assertTrue(root.completed)
        self.assertTrue(planning.completed)
        self.assertEqual(0, len(coordinator._suppressed_root_turns))
        self.assertEqual(
            {"thread-root", "thread-planning"},
            {
                params["threadId"]
                for method, params in client.requests
                if method == "turn/interrupt"
            },
        )

    def test_existing_native_goal_objective_and_budget_survive_project_lifecycle(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        client = _ScriptedClient(execution=_execution([str(image)]))
        client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "用户已有的稳定 Goal 目标",
            "status": "active",
            "tokenBudget": 54321,
        }
        harness = _ProjectHarness(client)
        harness.start()
        self.assertEqual("completed", harness.wait_terminal()["status"])
        goal_sets = [
            params
            for method, params in client.requests
            if method == "thread/goal/set"
        ]
        self.assertEqual(["active", "complete"], [item["status"] for item in goal_sets])
        self.assertTrue(
            all(item["objective"] == "用户已有的稳定 Goal 目标" for item in goal_sets)
        )
        self.assertTrue(all(item["tokenBudget"] == 54321 for item in goal_sets))

        failed_client = _ScriptedClient()
        failed_client.goals["thread-root"] = {
            "threadId": "thread-root",
            "objective": "启动失败也不得覆盖的 Goal",
            "status": "active",
            "tokenBudget": 777,
        }
        failed = _ProjectHarness(failed_client)
        project = failed.coordinator.prepare_project("thread-root", "建一个木屋")
        failed.coordinator.fail_project_start(
            project["project_id"], RuntimeError("turn/start failed")
        )
        self.assertEqual(
            "paused", failed_client.goals["thread-root"]["status"]
        )
        self.assertEqual(
            "启动失败也不得覆盖的 Goal",
            failed_client.goals["thread-root"]["objective"],
        )
        self.assertEqual(777, failed_client.goals["thread-root"]["tokenBudget"])

    def test_prepare_project_starts_all_four_worker_threads_concurrently(self) -> None:
        class _ParallelStartClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__()
                self.entered: set[str] = set()
                self.entered_lock = threading.Lock()
                self.all_entered = threading.Event()
                self.release = threading.Event()

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                source = params.get("threadSource")
                if method == "thread/start" and isinstance(source, str):
                    role = source.rsplit("/", 1)[-1]
                    with self.entered_lock:
                        self.entered.add(role)
                        if len(self.entered) == 4:
                            self.all_entered.set()
                    if not self.release.wait(2.0):
                        raise AssertionError("worker Thread starts were serialized")
                return super().request(method, params)

        client = _ParallelStartClient()
        harness = _ProjectHarness(client)
        outcome: dict[str, Any] = {}

        def prepare() -> None:
            try:
                outcome["result"] = harness.coordinator.prepare_project(
                    "thread-root", "build a project"
                )
            except Exception as exc:  # pragma: no cover - asserted below
                outcome["error"] = exc

        worker = threading.Thread(target=prepare)
        worker.start()
        entered_together = client.all_entered.wait(1.0)
        client.release.set()
        worker.join(3.0)

        self.assertTrue(entered_together)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(set(PROJECT_ROLE_ORDER[1:]), client.entered)
        project = harness.snapshot()["projects"][0]
        self.assertEqual(5, len(project["threads"]))

    def test_execution_does_not_start_when_running_record_cannot_persist(self) -> None:
        client = _ScriptedClient()
        harness = _ProjectHarness(client)
        original_write = harness.coordinator._write_registry
        failed = False

        def fail_execution_running_record() -> None:
            nonlocal failed
            with harness.coordinator._condition:
                execution_is_starting = any(
                    isinstance(project.get("threads"), Mapping)
                    and isinstance(project["threads"].get("execution"), Mapping)
                    and project["threads"]["execution"].get("status") == "running"
                    for project in harness.coordinator._projects
                )
            if execution_is_starting and not failed:
                failed = True
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected Execution running-record write failure",
                    http_status=503,
                )
            original_write()

        with mock.patch.object(
            harness.coordinator,
            "_write_registry",
            side_effect=fail_execution_running_record,
        ):
            project_id = harness.start()
            deadline = time.monotonic() + 3.0
            project = harness.snapshot()["projects"][0]
            while time.monotonic() < deadline and "before Execution" not in str(
                project.get("stage")
            ):
                time.sleep(0.01)
                project = harness.snapshot()["projects"][0]

            self.assertTrue(failed)
            self.assertEqual("running", project["status"])
            self.assertEqual("write_failed", harness.snapshot()["state_status"])
            self.assertIn("before Execution", project["stage"])
            self.assertFalse(
                any(
                    method == "turn/start"
                    and client.roles[params["threadId"]] == "execution"
                    for method, params in client.requests
                )
            )
            self.assertTrue(harness.coordinator.workflow_active())
            self.assertEqual("active", client.goals["thread-root"]["status"])
            with harness.coordinator._condition:
                internal = harness.coordinator._project(project_id)
                execution = harness.coordinator._thread(internal, "execution")
                self.assertNotEqual("running", execution["status"])
                self.assertNotIn(execution["thread_id"], harness.coordinator._pending)

        harness.coordinator.interrupt_project()

    def test_prepare_project_rolls_back_when_first_registry_write_fails(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            client = _ScriptedClient()
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=Path(directory) / "project-threads.json",
            )
            with mock.patch.object(
                coordinator,
                "_write_registry",
                side_effect=BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected first write failure",
                    http_status=503,
                ),
            ):
                with self.assertRaises(BridgeError) as raised:
                    coordinator.prepare_project("thread-root", "build a cabin")

            self.assertEqual(
                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                raised.exception.code,
            )
            snapshot = coordinator.snapshot(
                mode="team",
                writable=True,
                settings_state_status="ready",
            )
            self.assertEqual([], snapshot["projects"])
            self.assertEqual("write_failed", snapshot["state_status"])
            self.assertEqual({}, coordinator._pending)
            self.assertEqual({}, coordinator._thread_roles)
            self.assertEqual(0, coordinator._revision)
            self.assertFalse(
                any(method == "thread/start" for method, _params in client.requests)
            )

    def test_guidance_write_failure_rolls_back_capsule_and_never_steers(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            client = _ScriptedClient()
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=Path(directory) / "project-threads.json",
            )
            project = coordinator.prepare_project("thread-root", "build a cabin")
            snapshot = coordinator.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            planning_thread = next(
                item for item in snapshot["threads"] if item["role"] == "planning"
            )
            revision_before = coordinator._revision
            steer_before = sum(
                method == "turn/steer" for method, _params in client.requests
            )
            with mock.patch.object(
                coordinator,
                "_write_registry",
                side_effect=BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected guidance write failure",
                    http_status=503,
                ),
            ):
                with self.assertRaises(BridgeError) as raised:
                    coordinator.append_guidance(
                        project["project_id"],
                        planning_thread["thread_id"],
                        "add an exact dormer constraint",
                        model="gpt-pending",
                    )

            self.assertEqual(
                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                raised.exception.code,
            )
            self.assertEqual(revision_before, coordinator._revision)
            self.assertEqual(
                steer_before,
                sum(method == "turn/steer" for method, _params in client.requests),
            )
            with coordinator._condition:
                internal = coordinator._project(project["project_id"])
                self.assertEqual([], internal["_user_guidance"])
                self.assertEqual(0, internal["_guidance_revision"])
                planning = coordinator._thread(internal, "planning")
                self.assertNotIn("_next_model", planning)
            coordinator.interrupt_project()

    def test_thread_transfer_write_failure_preserves_old_identity(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            client = _ScriptedClient()
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=Path(directory) / "project-threads.json",
            )
            project = coordinator.prepare_project("thread-root", "build a cabin")
            snapshot = coordinator.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            planning_thread = next(
                item for item in snapshot["threads"] if item["role"] == "planning"
            )["thread_id"]
            self.assertTrue(coordinator.begin_thread_transfer(planning_thread))
            with mock.patch.object(
                coordinator,
                "_write_registry",
                side_effect=BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected transfer write failure",
                    http_status=503,
                ),
            ):
                with self.assertRaises(BridgeError):
                    coordinator.replace_thread_id(
                        planning_thread,
                        "thread-planning-replacement",
                    )

            with coordinator._condition:
                internal = coordinator._project(project["project_id"])
                self.assertEqual(
                    planning_thread,
                    coordinator._thread(internal, "planning")["thread_id"],
                )
                self.assertIn(planning_thread, coordinator._thread_roles)
                self.assertNotIn(
                    "thread-planning-replacement",
                    coordinator._thread_roles,
                )
            coordinator.finish_thread_transfer(planning_thread)
            coordinator.interrupt_project()

    def test_invalid_registry_blocks_team_and_single_scene_turns_without_overwrite(
        self,
    ) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            state_path = Path(directory) / "project-threads.json"
            invalid_bytes = b'{"schema":"hia-project-thread-registry/1","projects":['
            state_path.write_bytes(invalid_bytes)
            client = _ScriptedClient(root_thread_id="ordinary-thread")
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=state_path,
            )

            self.assertTrue(coordinator.workflow_active())
            coordinator.note_settings_change()
            self.assertEqual(invalid_bytes, state_path.read_bytes())
            with self.assertRaises(BridgeError) as team_error:
                coordinator.prepare_project("ordinary-thread", "build a cabin")
            self.assertEqual(
                "PROJECT_THREAD_REGISTRY_INVALID",
                team_error.exception.code,
            )
            self.assertFalse(
                any(method == "thread/start" for method, _params in client.requests)
            )

            session = BridgeSession(REPOSITORY_ROOT, client, EventBuffer())
            session.start_thread(team_override="single")
            session._project_threads = coordinator
            turn_starts_before = sum(
                method == "turn/start" for method, _params in client.requests
            )
            with self.assertRaises(BridgeError) as single_error:
                session.start_turn("change a known parameter", team_override="single")
            self.assertEqual(
                "PROJECT_THREAD_WORKFLOW_ACTIVE",
                single_error.exception.code,
            )
            self.assertEqual(
                turn_starts_before,
                sum(method == "turn/start" for method, _params in client.requests),
            )
            self.assertEqual(invalid_bytes, state_path.read_bytes())

    def test_root_attach_write_failure_never_activates_goal_or_driver(self) -> None:
        client = _ScriptedClient(block={"root"})
        harness = _ProjectHarness(client)
        project = harness.coordinator.prepare_project("thread-root", "build a cabin")
        harness.coordinator.begin_root_turn_request(project["project_id"])
        with (
            mock.patch.object(
                harness.coordinator,
                "_write_registry",
                side_effect=BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected root attach write failure",
                    http_status=503,
                ),
            ),
            mock.patch.object(harness.coordinator, "_drive_project") as drive,
        ):
            with self.assertRaises(BridgeError) as raised:
                harness.coordinator.attach_root_turn(
                    project["project_id"],
                    "turn-root-ack",
                )

        self.assertEqual(
            "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
            raised.exception.code,
        )
        drive.assert_not_called()
        self.assertNotIn("thread-root", client.goals)
        harness.coordinator.fail_project_start(project["project_id"], raised.exception)

    def test_user_stop_still_pauses_goal_and_interrupts_when_registry_is_down(self) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        project_id = harness.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            role == "planning" for role in client._active.values()
        ):
            time.sleep(0.01)
        self.assertTrue(any(role == "planning" for role in client._active.values()))
        with harness.coordinator._condition:
            harness.coordinator._registry_write_failed = True
            harness.coordinator._registry_write_error = "injected outage"
            harness.coordinator._state_status = "write_failed"
        with mock.patch.object(
            harness.coordinator,
            "_write_registry",
            side_effect=BridgeError(
                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                "injected stop write failure",
                http_status=503,
            ),
        ):
            outcome = harness.coordinator.interrupt_project()

        self.assertFalse(outcome["interrupted"])
        self.assertEqual([project_id], outcome["goal_close_failed_project_ids"])
        self.assertEqual("paused", client.goals["thread-root"]["status"])
        self.assertTrue(
            any(method == "turn/interrupt" for method, _params in client.requests)
        )
        project = harness.snapshot()["projects"][0]
        self.assertEqual("running", project["status"])
        self.assertTrue(harness.coordinator.workflow_active())

    def test_deleted_role_still_pauses_goal_and_interrupts_when_registry_is_down(
        self,
    ) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        project_id = harness.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            role == "planning" for role in client._active.values()
        ):
            time.sleep(0.01)
        self.assertTrue(any(role == "planning" for role in client._active.values()))
        planning_thread_id = next(
            thread_id
            for thread_id, role in client.roles.items()
            if role == "planning"
        )
        with mock.patch.object(
            harness.coordinator,
            "_write_registry",
            side_effect=BridgeError(
                "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                "injected deleted-role write failure",
                http_status=503,
            ),
        ):
            consumed = harness.coordinator.handle_client_event(
                {
                    "type": "codex_notification",
                    "method": "thread/deleted",
                    "params": {"threadId": planning_thread_id},
                }
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and client.goals.get(
                "thread-root", {}
            ).get("status") != "paused":
                time.sleep(0.01)

            self.assertTrue(consumed)
            self.assertEqual("paused", client.goals["thread-root"]["status"])
            self.assertTrue(
                any(
                    method == "turn/interrupt"
                    and params.get("threadId") == planning_thread_id
                    for method, params in client.requests
                )
            )
            project = harness.snapshot()["projects"][0]
            self.assertEqual("running", project["status"])
            self.assertEqual(project_id, project["project_id"])
            self.assertTrue(harness.coordinator.workflow_active())

    def test_terminal_write_failure_keeps_goal_and_writer_active_without_looping(
        self,
    ) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        client = _ScriptedClient(execution=_execution([str(image)]))
        harness = _ProjectHarness(client)
        original_write = harness.coordinator._write_registry
        terminal_write_attempted = threading.Event()

        def fail_terminal_write() -> None:
            with harness.coordinator._condition:
                terminal = any(
                    project.get("status") == "completed"
                    for project in harness.coordinator._projects
                )
            if terminal:
                terminal_write_attempted.set()
                raise BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected terminal write failure",
                    http_status=503,
                )
            original_write()

        with mock.patch.object(
            harness.coordinator,
            "_write_registry",
            side_effect=fail_terminal_write,
        ):
            harness.start()
            self.assertTrue(terminal_write_attempted.wait(3.0))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and client.goals.get(
                "thread-root", {}
            ).get("status") != "active":
                time.sleep(0.01)

            project = harness.snapshot()["projects"][0]
            self.assertEqual("running", project["status"])
            self.assertEqual("write_failed", harness.snapshot()["state_status"])
            self.assertIn("Terminal persistence failed", project["error"])
            self.assertEqual("active", client.goals["thread-root"]["status"])
            self.assertTrue(harness.coordinator.workflow_active())
            request_count = len(client.requests)
            time.sleep(0.1)
            self.assertEqual(request_count, len(client.requests))

        harness.coordinator.interrupt_project()

    def test_worker_identity_write_failure_prevents_project_start(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            client = _ScriptedClient()
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=Path(directory) / "project-threads.json",
            )
            original_write = coordinator._write_registry
            failure_lock = threading.Lock()
            failed = False

            def fail_first_worker_identity() -> None:
                nonlocal failed
                with coordinator._condition:
                    has_worker = any(
                        isinstance(project.get("threads"), Mapping)
                        and any(
                            role in project["threads"]
                            for role in PROJECT_ROLE_ORDER[1:]
                        )
                        for project in coordinator._projects
                    )
                with failure_lock:
                    if has_worker and not failed:
                        failed = True
                        raise BridgeError(
                            "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                            "injected worker identity write failure",
                            http_status=503,
                        )
                original_write()

            with (
                mock.patch.object(
                    coordinator,
                    "_write_registry",
                    side_effect=fail_first_worker_identity,
                ),
                mock.patch.object(coordinator, "_drive_project") as drive,
            ):
                with self.assertRaises(BridgeError):
                    coordinator.prepare_project("thread-root", "build a cabin")

            self.assertTrue(failed)
            drive.assert_not_called()
            self.assertGreaterEqual(
                sum(method == "thread/start" for method, _params in client.requests),
                1,
            )
            self.assertEqual("paused", client.goals["thread-root"]["status"])
            project = coordinator.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            self.assertNotEqual("running", project["status"])

    def test_observed_root_write_failure_pauses_without_starting_driver(self) -> None:
        client = _ScriptedClient(block={"root"})
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            client,
            EventBuffer(),
            turn_timeout_seconds=1.0,
        )
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project("thread-root", "build a cabin")
        # Model the response/event race after turn/start has identified the
        # exact root Turn but before attach_root_turn persists project ownership.
        # A genuinely pre-ACK notification is intentionally buffered and is a
        # different path covered by the pre-ACK tests below.
        with coordinator._condition:
            pending = coordinator._pending["thread-root"]
            pending.turn_id = "turn-observed"
            pending.request_inflight = False
        client._active[("thread-root", "turn-observed")] = "root"
        with (
            mock.patch.object(
                coordinator,
                "_write_registry",
                side_effect=BridgeError(
                    "PROJECT_THREAD_REGISTRY_UNAVAILABLE",
                    "injected observed-root write failure",
                    http_status=503,
                ),
            ),
            mock.patch.object(coordinator, "_drive_project") as drive,
        ):
            coordinator.handle_client_event(
                {
                    "type": "codex_notification",
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread-root",
                        "turn": {"id": "turn-observed", "status": "inProgress"},
                    },
                }
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and client.goals.get(
                "thread-root", {}
            ).get("status") != "paused":
                time.sleep(0.01)

            drive.assert_not_called()
            self.assertEqual("paused", client.goals["thread-root"]["status"])
            self.assertTrue(
                any(method == "turn/interrupt" for method, _params in client.requests)
            )
            self.assertTrue(coordinator.workflow_active())

    def test_large_public_snapshot_and_registry_keep_only_collaboration_count(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            state_path = Path(directory) / "project-threads.json"
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                _ScriptedClient(),
                EventBuffer(),
                state_path=state_path,
            )
            large_event = {
                "item_id": "item-" + ("x" * 512),
                "tool": "spawnAgent",
                "status": "completed",
                "child_thread_ids": ["child-" + ("y" * 256)],
            }
            with coordinator._condition:
                for project_index in range(256):
                    project_id = f"project-{project_index:03d}"
                    project = {
                        "project_id": project_id,
                        "title": f"Archived project {project_index}",
                        "status": "completed",
                        "stage": "Delivered",
                        "progress": coordinator._progress(1, 1, "completed"),
                        "updated_at": float(project_index),
                        "root_thread_id": f"thread-{project_index:03d}-supervisor",
                        "threads": {},
                    }
                    coordinator._projects.append(project)
                    for role in PROJECT_ROLE_ORDER:
                        thread_id = f"thread-{project_index:03d}-{role}"
                        coordinator._record_thread(
                            project,
                            role,
                            thread_id,
                            f"Archived project {project_index} | {role}",
                            "gpt-test",
                        )
                        project["threads"][role]["status"] = "completed"
                        project["threads"][role]["collaboration"] = {
                            "mode": "used-with-real-events",
                            "events": [copy.deepcopy(large_event) for _index in range(8)],
                            "event_count": 8,
                        }
                self.assertTrue(coordinator._try_write_registry())

            snapshot = coordinator.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )
            encoded_snapshot = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            self.assertEqual(256, len(snapshot["projects"]))
            self.assertLess(len(encoded_snapshot), 2 * 1_048_576)
            for project in snapshot["projects"]:
                self.assertEqual(5, len(project["threads"]))
                for thread in project["threads"]:
                    self.assertNotIn("events", thread["collaboration"])
                    self.assertEqual(8, thread["collaboration"]["event_count"])
                    self.assertTrue(thread["thread_id"])
                    self.assertTrue(thread["role_title"])
            self.assertLess(state_path.stat().st_size, 16 * 1_048_576)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(256, len(persisted["projects"]))
            self.assertNotIn(
                "events",
                persisted["projects"][0]["threads"][0]["collaboration"],
            )

            reloaded = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                _ScriptedClient(),
                EventBuffer(),
                state_path=state_path,
            )
            reloaded_snapshot = reloaded.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )
            self.assertEqual(256, len(reloaded_snapshot["projects"]))
            self.assertEqual(
                8,
                reloaded_snapshot["projects"][0]["threads"][0][
                    "collaboration"
                ]["event_count"],
            )

    def test_restart_pauses_stale_native_goal_before_releasing_writer(self) -> None:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            state_path = Path(directory) / "project-threads.json"
            client = _ScriptedClient()
            first = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=state_path,
                turn_timeout_seconds=1.0,
            )
            first.prepare_project("thread-root", "建一个木屋")
            client.goals["thread-root"] = {
                "threadId": "thread-root",
                "objective": "建一个木屋",
                "status": "active",
                "tokenBudget": None,
            }
            self.assertTrue(first.workflow_active())

            restarted = ProjectThreadCoordinator(
                REPOSITORY_ROOT,
                client,
                EventBuffer(),
                state_path=state_path,
                turn_timeout_seconds=1.0,
            )
            before = restarted.snapshot(
                mode="team",
                writable=True,
                settings_state_status="ready",
            )["projects"][0]
            self.assertEqual("running", before["status"])
            self.assertTrue(restarted.workflow_active())

            outcome = restarted.reconcile_restarted_goals()
            project = restarted.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual("interrupted", project["status"])
            self.assertIn("Bridge", project["stage"])
            self.assertFalse(restarted.workflow_active())
            self.assertEqual("interrupted", persisted["projects"][0]["status"])
            self.assertEqual("paused", client.goals["thread-root"]["status"])
            self.assertEqual([project["project_id"]], outcome["reconciled_project_ids"])

    def test_restart_goal_pause_failure_keeps_writer_locked_and_running(self) -> None:
        class _PauseFailureClient(_ScriptedClient):
            fail_pause = False

            def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
                if (
                    self.fail_pause
                    and method == "thread/goal/set"
                    and params.get("status") == "paused"
                ):
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                    raise BridgeError(
                        "CODEX_RPC_ERROR", "pause rejected", http_status=502
                    )
                return super().request(method, params)

        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            state_path = Path(directory) / "project-threads.json"
            client = _PauseFailureClient()
            first = ProjectThreadCoordinator(
                REPOSITORY_ROOT, client, EventBuffer(), state_path=state_path
            )
            project_id = first.prepare_project("thread-root", "建一个木屋")[
                "project_id"
            ]
            client.goals["thread-root"] = {
                "threadId": "thread-root",
                "objective": "建一个木屋",
                "status": "active",
                "tokenBudget": None,
            }
            client.fail_pause = True
            restarted = ProjectThreadCoordinator(
                REPOSITORY_ROOT, client, EventBuffer(), state_path=state_path
            )
            outcome = restarted.reconcile_restarted_goals()
            project = restarted.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual([project_id], outcome["failed_project_ids"])
            self.assertEqual("running", project["status"])
            self.assertIn("Goal", project["stage"])
            self.assertEqual("running", persisted["projects"][0]["status"])
            self.assertTrue(restarted.workflow_active())
            self.assertEqual("active", client.goals["thread-root"]["status"])

    def test_process_exit_keeps_writer_until_restart_goal_pause_is_confirmed(self) -> None:
        class _PauseFailureClient(_ScriptedClient):
            fail_pause = True

            def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
                if (
                    self.fail_pause
                    and method == "thread/goal/set"
                    and params.get("status") == "paused"
                ):
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                    raise BridgeError(
                        "CODEX_RPC_ERROR", "pause rejected", http_status=502
                    )
                return super().request(method, params)

        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_tmp) as directory:
            state_path = Path(directory) / "project-threads.json"
            client = _PauseFailureClient()
            client.fail_pause = False
            coordinator = ProjectThreadCoordinator(
                REPOSITORY_ROOT, client, EventBuffer(), state_path=state_path
            )
            project_id = coordinator.prepare_project("thread-root", "建一个木屋")[
                "project_id"
            ]
            client.goals["thread-root"] = {
                "threadId": "thread-root",
                "objective": "建一个木屋",
                "status": "active",
                "tokenBudget": None,
            }
            coordinator.handle_client_event(
                {"type": "process_exit", "returncode": 1}
            )
            exited = coordinator.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            self.assertEqual("running", exited["status"])
            self.assertTrue(coordinator.workflow_active())

            client.fail_pause = True
            restarted = ProjectThreadCoordinator(
                REPOSITORY_ROOT, client, EventBuffer(), state_path=state_path
            )
            failed = restarted.reconcile_restarted_goals()
            self.assertEqual([project_id], failed["failed_project_ids"])
            self.assertTrue(restarted.workflow_active())
            self.assertEqual(
                "running",
                restarted.snapshot(
                    mode="team", writable=True, settings_state_status="ready"
                )["projects"][0]["status"],
            )

            client.fail_pause = False
            recovered = restarted.reconcile_restarted_goals()
            project = restarted.snapshot(
                mode="team", writable=True, settings_state_status="ready"
            )["projects"][0]
            self.assertEqual([project_id], recovered["reconciled_project_ids"])
            self.assertEqual("interrupted", project["status"])
            self.assertFalse(restarted.workflow_active())
            self.assertEqual("paused", client.goals["thread-root"]["status"])

    def test_observed_root_attach_is_idempotent_after_pending_was_consumed(self) -> None:
        client = _ScriptedClient()
        harness = _ProjectHarness(client)
        project = harness.coordinator.prepare_project(
            "thread-root", "build a project"
        )
        launched = threading.Event()

        with mock.patch.object(
            harness.coordinator,
            "_drive_project",
            side_effect=lambda _project_id: launched.set(),
        ) as drive_project:
            harness.coordinator.begin_root_turn_request(project["project_id"])
            harness.coordinator.handle_client_event(
                {
                    "type": "codex_notification",
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread-root",
                        "turn": {"id": "turn-root", "status": "inProgress"},
                    },
                }
            )
            self.assertFalse(launched.wait(0.05))
            harness.coordinator.attach_root_turn(
                project["project_id"], "turn-root"
            )
            self.assertTrue(launched.wait(1.0))
            with harness.coordinator._condition:
                harness.coordinator._pending.pop("thread-root", None)

            harness.coordinator.attach_root_turn(
                project["project_id"], "turn-root"
            )

        self.assertEqual(1, drive_project.call_count)

    def test_pre_ack_terminal_candidate_is_suppressed_without_false_failure(self) -> None:
        client = _ScriptedClient(block={"root"})
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project("thread-root", "建一个木屋")
        coordinator.begin_root_turn_request(project["project_id"])
        candidate_started = {
            "type": "codex_notification",
            "method": "turn/started",
            "params": {
                "threadId": "thread-root",
                "turn": {"id": "turn-unowned", "status": "inProgress"},
            },
        }
        candidate_completed = {
            "type": "codex_notification",
            "method": "turn/completed",
            "params": {
                "threadId": "thread-root",
                "turn": {"id": "turn-unowned", "status": "completed"},
            },
        }
        self.assertTrue(coordinator.handle_client_event(candidate_started))
        self.assertTrue(coordinator.handle_client_event(candidate_completed))
        with mock.patch.object(coordinator, "_drive_project") as driver:
            coordinator.attach_root_turn(project["project_id"], "turn-acknowledged")
        self.assertEqual(1, driver.call_count)
        with coordinator._condition:
            pending = coordinator._pending["thread-root"]
            self.assertEqual("turn-acknowledged", pending.turn_id)
            self.assertFalse(pending.completed)
            self.assertEqual(0, len(coordinator._suppressed_root_turns))
        self.assertFalse(
            any(method == "turn/interrupt" for method, _params in client.requests)
        )
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        self.assertEqual("running", snapshot["status"])

    def test_missing_root_completion_event_reconciles_before_authorization(self) -> None:
        class _HistoryClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"supervisor"})

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "thread/read":
                    copied = copy.deepcopy(dict(params))
                    with self._lock:
                        self.requests.append((method, copied))
                    return {
                        "thread": {
                            "id": self.root_thread_id,
                            "status": {"type": "idle"},
                            "turns": [
                                {
                                    "id": "turn-root",
                                    "status": "completed",
                                    "items": [
                                        {
                                            "id": "message-root",
                                            "type": "agentMessage",
                                            "text": json.dumps(
                                                _brief(), ensure_ascii=False
                                            ),
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                return super().request(method, params)

        client = _HistoryClient()
        harness = _ProjectHarness(client)
        spec = harness.coordinator.prepare_project(
            "thread-root", "建一个木屋"
        )
        harness.coordinator.attach_root_turn(
            spec["project_id"], "turn-root"
        )

        deadline = time.monotonic() + 2.0
        authorization = None
        while time.monotonic() < deadline:
            authorization = next(
                (
                    params
                    for method, params in client.requests
                    if method == "turn/start"
                    and params["threadId"] == "thread-root"
                    and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
                ),
                None,
            )
            if authorization is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(authorization)
        self.assertTrue(
            any(method == "thread/read" for method, _params in client.requests)
        )
        harness.coordinator.interrupt_project()

    def test_history_probe_timeout_falls_back_to_late_root_completion_event(self) -> None:
        class _ProbeTimeoutClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"supervisor"})
                self.probe_attempted = threading.Event()

            def request_with_timeout(
                self,
                method: str,
                params: dict[str, Any],
                *,
                timeout_seconds: float,
            ) -> Any:
                if method == "thread/read":
                    _ = timeout_seconds
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(params)))
                    self.probe_attempted.set()
                    raise BridgeError(
                        "CODEX_REQUEST_TIMEOUT",
                        "bounded history probe timed out",
                        http_status=504,
                    )
                return super().request_with_timeout(
                    method, params, timeout_seconds=timeout_seconds
                )

        client = _ProbeTimeoutClient()
        harness = _ProjectHarness(client)
        spec = harness.coordinator.prepare_project("thread-root", "建一个木屋")
        harness.coordinator.attach_root_turn(spec["project_id"], "turn-root")
        self.assertTrue(client.probe_attempted.wait(1.0))

        client.complete_root("turn-root")
        deadline = time.monotonic() + 2.0
        authorization = None
        while time.monotonic() < deadline:
            authorization = next(
                (
                    params
                    for method, params in client.requests
                    if method == "turn/start"
                    and params["threadId"] == client.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
                ),
                None,
            )
            if authorization is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(authorization)
        self.assertEqual("running", harness.snapshot()["projects"][0]["status"])
        harness.coordinator.interrupt_project()

    def test_queued_guidance_capsule_is_consumed_by_next_workflow_turn(self) -> None:
        client = _ScriptedClient(block={"supervisor"})
        harness = _ProjectHarness(client)
        spec = harness.coordinator.prepare_project("thread-root", "建一个木屋")
        attachment_dir = (
            REPOSITORY_ROOT / ".runtime" / "attachments" / "thread-root"
        )
        attachment_dir.mkdir(parents=True, exist_ok=True)
        image = attachment_dir / "queued-guidance.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.addCleanup(image.unlink, missing_ok=True)

        accepted = harness.coordinator.accept_supervisor_guidance(
            spec["project_id"],
            "用户补充：保留更宽的门洞。",
            local_image_paths=(str(image.resolve()),),
        )
        self.assertTrue(accepted["guidance_accepted"])
        self.assertEqual(
            {"mode": "queued", "project_id": spec["project_id"]},
            accepted["workflow_delivery"],
        )

        client.complete_root("turn-root")
        harness.coordinator.attach_root_turn(spec["project_id"], "turn-root")
        deadline = time.monotonic() + 2.0
        planning = None
        while time.monotonic() < deadline:
            planning = next(
                (
                    params
                    for method, params in client.requests
                    if method == "turn/start"
                    and client.roles[params["threadId"]] == "planning"
                ),
                None,
            )
            if planning is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(planning)
        self.assertIn("AUTHORITATIVE USER GUIDANCE", planning["input"][0]["text"])
        self.assertIn("保留更宽的门洞", planning["input"][0]["text"])
        self.assertIn(
            {"type": "localImage", "path": str(image.resolve())},
            planning["input"],
        )
        harness.coordinator.interrupt_project()

    def test_root_timeout_interrupts_original_turn_then_runs_bounded_recovery(self) -> None:
        class _ActiveHistoryClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"supervisor"})

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "thread/read":
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                    return {
                        "thread": {
                            "id": self.root_thread_id,
                            "status": {"type": "active"},
                            "turns": [
                                {"id": "turn-root", "status": "inProgress", "items": []}
                            ],
                        }
                    }
                return super().request(method, params)

        client = _ActiveHistoryClient()
        coordinator = ProjectThreadCoordinator(
            REPOSITORY_ROOT,
            client,
            EventBuffer(),
            turn_timeout_seconds=0.05,
        )
        client.set_event_sink(coordinator.handle_client_event)
        spec = coordinator.prepare_project("thread-root", "建一个木屋")
        coordinator.attach_root_turn(spec["project_id"], "turn-root")

        deadline = time.monotonic() + 2.0
        authorization = None
        while time.monotonic() < deadline:
            authorization = next(
                (
                    params
                    for method, params in client.requests
                    if method == "turn/start"
                    and params["threadId"] == client.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
                ),
                None,
            )
            if authorization is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(authorization)
        self.assertTrue(
            any(
                method == "turn/interrupt"
                and params == {"threadId": "thread-root", "turnId": "turn-root"}
                for method, params in client.requests
            )
        )
        correction = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == client.root_thread_id
            and params.get("outputSchema") == SUPERVISOR_SCHEMA
        ]
        self.assertEqual(1, len(correction))
        self.assertIn("previous root/Goal continuation", correction[0]["input"][0]["text"])
        self.assertEqual("active", client.goals[client.root_thread_id]["status"])
        coordinator.interrupt_project()

    def test_goal_continuation_output_is_replaced_by_bounded_root_brief_turn(self) -> None:
        class _GoalContinuationClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"supervisor"})

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "thread/read":
                    copied = copy.deepcopy(dict(params))
                    with self._lock:
                        self.requests.append((method, copied))
                        self.goals[self.root_thread_id]["status"] = "blocked"
                    return {
                        "thread": {
                            "id": self.root_thread_id,
                            "status": {"type": "idle"},
                            "turns": [
                                {
                                    "id": "goal-continuation-turn",
                                    "status": "completed",
                                    "items": [
                                        {
                                            "id": "message-goal",
                                            "type": "agentMessage",
                                            "text": json.dumps(
                                                {
                                                    "status": "blocked",
                                                    "goal": "build a project",
                                                }
                                            ),
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                return super().request(method, params)

        client = _GoalContinuationClient()
        harness = _ProjectHarness(client)
        spec = harness.coordinator.prepare_project(
            "thread-root", "建一个木屋"
        )
        harness.coordinator.attach_root_turn(
            spec["project_id"], "turn-root"
        )

        deadline = time.monotonic() + 2.0
        correction = None
        while time.monotonic() < deadline:
            correction = next(
                (
                    params
                    for method, params in client.requests
                    if method == "turn/start"
                    and params["threadId"] == "thread-root"
                    and params.get("outputSchema") == SUPERVISOR_SCHEMA
                ),
                None,
            )
            if correction is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(correction)
        self.assertIn("intake brief", correction["input"][0]["text"])
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if client.goals[client.root_thread_id]["status"] == "active":
                break
            time.sleep(0.01)
        self.assertEqual("active", client.goals[client.root_thread_id]["status"])
        harness.coordinator.interrupt_project()

    def test_planning_repairs_floors_before_supervisor_without_regressing_detail(self) -> None:
        complete = _plan()
        initial = copy.deepcopy(complete)
        vague_stage = copy.deepcopy(_vague_plan()["stages"][0])
        vague_step = vague_stage["ordered_construction_steps"][0]
        vague_stage["ordered_construction_steps"] = [
            copy.deepcopy(vague_step) for _ in range(3)
        ]
        initial["stages"][1] = copy.deepcopy(vague_stage)
        initial["stages"][2] = copy.deepcopy(vague_stage)
        initial["stages"][2]["title"] = "另一个仍然不足的信息阶段"
        regressed = copy.deepcopy(complete)
        regressed["stages"][0]["ordered_construction_steps"][0][
            "expected_result"
        ] = "Walls connect to roof supports with no overlap."
        validator = ProjectThreadCoordinator(
            REPOSITORY_ROOT, _ScriptedClient(), EventBuffer()
        )
        initial_errors = validator._validate_plan(initial, "建一个木屋", _brief())
        self.assertTrue(any("10000" in error for error in initial_errors))
        self.assertTrue(
            any("stage 2 lacks the detailed construction" in error for error in initial_errors)
        )
        self.assertTrue(
            any("stage 2 step 1 lacks enough" in error for error in initial_errors)
        )
        self.assertEqual(
            [], validator._validate_plan(regressed, "建一个木屋", _brief())
        )
        self.assertTrue(validator._plan_regression_errors(initial, regressed))

        client = _ScriptedClient(
            planning=complete,
            planning_sequence=[initial, regressed, complete],
            block={"supervisor"},
        )
        harness = _ProjectHarness(client)
        harness.start("建一个木屋")

        deadline = time.monotonic() + 2.0
        authorization = None
        while time.monotonic() < deadline:
            authorization = next(
                (
                    (index, params)
                    for index, (method, params) in enumerate(client.requests)
                    if method == "turn/start"
                    and params["threadId"] == client.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
                ),
                None,
            )
            if authorization is not None:
                break
            time.sleep(0.01)

        self.assertIsNotNone(authorization)
        authorization_index, authorization_params = authorization
        planning_indexes = [
            index
            for index, (method, params) in enumerate(client.requests)
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        ]
        self.assertEqual(3, len(planning_indexes))
        self.assertLess(max(planning_indexes), authorization_index)
        self.assertIn(
            complete["stages"][0]["ordered_construction_steps"][0][
                "expected_result"
            ],
            authorization_params["input"][0]["text"],
        )
        harness.coordinator.interrupt_project()

    def test_exact_five_roles_and_non_execution_hia_is_disabled(self) -> None:
        client = _ScriptedClient()
        harness = _ProjectHarness(client)
        harness.coordinator.prepare_project("thread-root", "建一个木屋")
        project = harness.snapshot()["projects"][0]

        self.assertEqual(list(PROJECT_ROLE_ORDER), [item["role"] for item in project["threads"]])
        starts = {
            params["threadSource"].rsplit("/", 1)[-1]: params
            for method, params in client.requests
            if method == "thread/start" and "threadSource" in params
        }
        for role in ("planning", "visual_review", "technical_review"):
            self.assertEqual("read-only", starts[role]["sandbox"])
            self.assertEqual(
                {**PROJECT_HIA_DISABLE_CONFIG, "multi_agent_mode": "proactive"},
                starts[role]["config"],
            )
        self.assertEqual("workspace-write", starts["execution"]["sandbox"])
        self.assertEqual(
            {
                **PROJECT_HIA_ENABLE_CONFIG,
                "multi_agent_mode": "explicitRequestOnly",
            },
            starts["execution"]["config"],
        )
        for thread in project["threads"]:
            descriptor = harness.coordinator.transfer_descriptor(thread["thread_id"])
            expected_read_only = thread["role"] != "execution"
            self.assertEqual(
                "proactive" if expected_read_only else "explicitRequestOnly",
                descriptor["config"]["multi_agent_mode"],
            )
            self.assertEqual(
                not expected_read_only,
                descriptor["config"]["mcp_servers.hia_mcp_v2.enabled"],
            )

    def test_real_collaboration_events_mark_read_only_role_not_execution(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        planning = _plan()
        planning["collaboration_mode"] = "used-with-real-events"
        client = _ScriptedClient(
            planning=planning,
            execution=_execution([str(image)]),
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()
        self.assertEqual("completed", project["status"])
        roles = {thread["role"]: thread for thread in harness.snapshot()["projects"][0]["threads"]}
        self.assertEqual("used-with-real-events", roles["planning"]["collaboration"]["mode"])
        self.assertNotIn("events", roles["planning"]["collaboration"])
        self.assertGreater(roles["planning"]["collaboration"]["event_count"], 0)
        self.assertEqual("serial-fallback", roles["execution"]["collaboration"]["mode"])
        with harness.coordinator._condition:
            internal = harness.coordinator._project(project["project_id"])
            planning_record = harness.coordinator._thread(internal, "planning")
            self.assertTrue(planning_record["collaboration"]["events"])

    def test_bridge_derives_collaboration_mode_without_retrying_role(self) -> None:
        directory, image = self._valid_image()
        self.addCleanup(directory.cleanup)
        planning = _plan()
        planning["collaboration_mode"] = "项目团队"
        client = _ScriptedClient(
            planning=planning,
            execution=_execution([str(image)]),
        )
        harness = _ProjectHarness(client)
        harness.start()
        project = harness.wait_terminal()

        self.assertEqual("completed", project["status"])
        planning_thread = next(
            thread
            for thread in harness.snapshot()["projects"][0]["threads"]
            if thread["role"] == "planning"
        )
        self.assertEqual(
            "serial-fallback", planning_thread["collaboration"]["mode"]
        )
        planning_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        ]
        self.assertEqual(1, len(planning_starts))

    def test_model_change_during_active_turn_is_reported_as_pending(self) -> None:
        client = _ScriptedClient(block={"planning"})
        harness = _ProjectHarness(client)
        project_id = harness.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            planning = next(
                item
                for item in harness.snapshot()["projects"][0]["threads"]
                if item["role"] == "planning"
            )
            if planning["status"] == "running":
                break
            time.sleep(0.01)
        harness.coordinator.append_guidance(
            project_id,
            planning["thread_id"],
            "后续使用新模型继续，但当前 Turn 只追加本条指导。",
            model="gpt-new",
        )
        planning = next(
            item
            for item in harness.snapshot()["projects"][0]["threads"]
            if item["role"] == "planning"
        )
        self.assertIsNone(planning["model"])
        self.assertEqual("gpt-new", planning["pending_model"])
        self.assertEqual("随下一条指导生效", planning["model_application"])
        self.assertTrue(
            any(method == "turn/steer" for method, _params in client.requests)
        )
        harness.coordinator.interrupt_project()


class SessionProjectIntegrationTests(unittest.TestCase):
    def _session(
        self,
        client: _ScriptedClient,
        *,
        project_root: Path = REPOSITORY_ROOT,
    ) -> BridgeSession:
        self.events = EventBuffer()
        session = BridgeSession(project_root, client, self.events)
        session.update_project_team_mode(mode="team")
        session.start_thread()
        return session

    def test_root_turn_preserves_user_text_attachment_and_initial_titles(self) -> None:
        attachments = REPOSITORY_ROOT / ".runtime" / "attachments"
        attachments.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=attachments)
        self.addCleanup(directory.cleanup)
        root_id = Path(directory.name).name
        image = Path(directory.name) / "reference.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _ScriptedClient(
            root_thread_id=root_id,
            authorization=_plan(title="监督建议的另一个项目名"),
        )
        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_dir = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_dir.cleanup)
        capture = Path(capture_dir.name) / "review.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\n")
        client.execution = _execution([str(capture)])
        session = self._session(client)

        result = session.start_turn(
            "建一个木屋",
            local_image_paths=[str(image)],
            team_override="team",
        )
        self.assertEqual("team", result["routing"])
        root_start = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == root_id
            and params.get("outputSchema") == SUPERVISOR_SCHEMA
        )
        self.assertEqual("text", root_start["input"][0]["type"])
        intake = root_start["input"][0]["text"]
        self.assertIn("Supervisor's first intake Turn", intake)
        self.assertIn("USER TASK:\n建一个木屋", intake)
        self.assertIn("Do not use tools", intake)
        self.assertIn("Do not wait for Planning", intake)
        self.assertIn("manage a native Goal", intake)
        self.assertIn("project-mode validation", intake)
        self.assertIn("scene_task_eligibility.decision", intake)
        self.assertEqual(
            {"type": "localImage", "path": str(image.resolve())},
            root_start["input"][1],
        )
        self.assertEqual(2, len(root_start["input"]))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            project = session.project_team_snapshot()["projects"][0]
            if project["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual("completed", project["status"])
        self.assertEqual("建一个木屋", project["title"])
        self.assertTrue(
            all(thread["title"].startswith("建一个木屋｜") for thread in project["threads"])
        )

    def test_non_scene_plugin_validation_is_not_applicable_before_execution(self) -> None:
        class _IneligibleClient(_ScriptedClient):
            def _payload(
                self,
                role: str,
                params: Mapping[str, Any],
                root_turn: bool,
            ) -> Mapping[str, Any]:
                if root_turn:
                    return {
                        **_brief(),
                        "scene_task_eligibility": {
                            "decision": "not_applicable",
                            "reason": "This requests repository plugin-mode validation, not Houdini scene work.",
                        },
                    }
                return super()._payload(role, params, root_turn)

        client = _IneligibleClient()
        session = self._session(client)
        session.start_turn(
            "验证插件项目模式是否能正常启动",
            team_override="team",
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            project = session.project_team_snapshot()["projects"][0]
            if project["status"] != "running":
                break
            time.sleep(0.01)

        self.assertEqual("not_applicable", project["status"])
        self.assertIn("不适用于 Houdini 项目团队", project["stage"])
        self.assertIn("普通任务/单个 AI", project["error"])
        self.assertFalse(session._project_threads.workflow_active())
        self.assertFalse(
            any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            )
        )
        self.assertEqual(
            ["active", "paused"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )

    def test_guidance_after_root_brief_reclassifies_before_not_applicable_commit(
        self,
    ) -> None:
        class _IntakeRaceClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__()
                self.session: BridgeSession | None = None
                self.intake_count = 0
                self.guidance_receipt: dict[str, Any] | None = None

            def _payload(
                self,
                role: str,
                params: Mapping[str, Any],
                root_turn: bool,
            ) -> Mapping[str, Any]:
                if root_turn:
                    self.intake_count += 1
                    if self.intake_count == 1:
                        return {
                            **_brief(),
                            "scene_task_eligibility": {
                                "decision": "not_applicable",
                                "reason": "The original text appears to request plugin validation.",
                            },
                        }
                    return _brief()
                return super()._payload(role, params, root_turn)

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                is_first_intake = (
                    method == "turn/start"
                    and params.get("threadId") == self.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_SCHEMA
                    and self.intake_count == 0
                )
                result = super().request(method, params)
                if is_first_intake:
                    if self.session is None:
                        raise AssertionError("test session was not attached")
                    project = self.session.project_team_snapshot()["projects"][0]
                    self.guidance_receipt = self.session.append_project_guidance(
                        project_id=project["project_id"],
                        thread_id=self.root_thread_id,
                        text=(
                            "这是实际的 Houdini 场景任务：请在当前项目里建造完整木屋，"
                            "不是验证插件。"
                        ),
                    )
                return result

        client = _IntakeRaceClient()
        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        capture = Path(capture_directory.name) / "intake-race-review.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\n")
        client.execution = _execution([str(capture)])
        session = self._session(client)
        client.session = session

        session.start_turn(
            "验证插件项目模式是否能正常启动",
            team_override="team",
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            project = session.project_team_snapshot()["projects"][0]
            if project["status"] != "running":
                break
            time.sleep(0.01)

        self.assertEqual("completed", project["status"])
        self.assertEqual(2, client.intake_count)
        self.assertIsNotNone(client.guidance_receipt)
        assert client.guidance_receipt is not None
        self.assertTrue(client.guidance_receipt["guidance_accepted"])
        self.assertEqual(
            "queued", client.guidance_receipt["workflow_delivery"]["mode"]
        )
        correction_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == client.root_thread_id
            and params.get("outputSchema") == SUPERVISOR_SCHEMA
        ]
        self.assertEqual(2, len(correction_starts))
        correction_text = correction_starts[1]["input"][0]["text"]
        self.assertIn("AUTHORITATIVE USER GUIDANCE", correction_text)
        self.assertIn("不是验证插件", correction_text)
        self.assertTrue(
            any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            )
        )
        self.assertNotIn(
            "paused",
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )

    def test_global_guidance_reclassifies_eligible_direct_intake_before_planning(
        self,
    ) -> None:
        guidance = (
            "This is now a complete reference-driven cabin asset: classify it Full "
            "and preserve a continuous glazed roof monitor as a hard constraint."
        )
        initial_brief = _brief()
        initial_brief["task_depth"] = "direct"
        initial_brief["summary"] = "Change one already known cabin parameter."
        revised_brief = _brief()
        revised_brief["task_depth"] = "full"
        revised_brief["hard_constraints"] = [
            *revised_brief["hard_constraints"],
            guidance,
        ]
        revised_plan = _plan()
        revised_plan["hard_constraints"] = [
            *revised_plan["hard_constraints"],
            guidance,
        ]

        class _EligibleIntakeRaceClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(
                    brief=initial_brief,
                    planning=revised_plan,
                    authorization=revised_plan,
                )
                self.session: BridgeSession | None = None
                self.intake_count = 0
                self.guidance_receipt: dict[str, Any] | None = None

            def _payload(
                self,
                role: str,
                params: Mapping[str, Any],
                root_turn: bool,
            ) -> Mapping[str, Any]:
                if root_turn:
                    self.intake_count += 1
                    return copy.deepcopy(
                        initial_brief if self.intake_count == 1 else revised_brief
                    )
                return super()._payload(role, params, root_turn)

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                is_first_intake = (
                    method == "turn/start"
                    and params.get("threadId") == self.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_SCHEMA
                    and self.intake_count == 0
                )
                result = super().request(method, params)
                if is_first_intake:
                    if self.session is None:
                        raise AssertionError("test session was not attached")
                    self.guidance_receipt = (
                        self.session.append_active_supervisor_guidance(text=guidance)
                    )
                return result

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        capture = Path(capture_directory.name) / "eligible-intake-race.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _EligibleIntakeRaceClient()
        client.execution = _execution([str(capture)])
        session = self._session(client)
        client.session = session

        session.start_turn("Change the known cabin width parameter", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertEqual(2, client.intake_count)
        self.assertIsNotNone(client.guidance_receipt)
        planning_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        ]
        authorization_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "supervisor"
            and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        ]
        execution_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and params.get("outputSchema") == EXECUTION_SCHEMA
        ]
        self.assertTrue(planning_starts)
        self.assertTrue(authorization_starts)
        self.assertTrue(execution_starts)
        self.assertIn(guidance, planning_starts[-1]["input"][0]["text"])
        self.assertIn(guidance, authorization_starts[0]["input"][0]["text"])
        self.assertIn('"task_depth":"full"', authorization_starts[0]["input"][0]["text"])
        self.assertIn(guidance, execution_starts[0]["input"][0]["text"])
        self.assertIn('"task_depth":"full"', execution_starts[0]["input"][0]["text"])

    def test_root_runtime_defaults_seed_all_roles_and_transfer_descriptors(self) -> None:
        client = _ScriptedClient(
            block={"root", "planning"},
            response_model="gpt-default-project",
            response_effort="ultra",
            response_service_tier="priority",
        )
        session = self._session(client)
        session.start_turn("建一个木屋", team_override="team")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "planning"
                for method, params in client.requests
            ):
                break
            time.sleep(0.01)
        project = session.project_team_snapshot()["projects"][0]
        self.assertEqual(
            {"gpt-default-project"},
            {thread["model"] for thread in project["threads"]},
        )
        for thread in project["threads"]:
            descriptor = session._project_threads.transfer_descriptor(
                thread["thread_id"]
            )
            self.assertEqual("gpt-default-project", descriptor["model"])
            self.assertEqual("ultra", descriptor["effort"])
            self.assertEqual("priority", descriptor["service_tier"])
        worker_starts = [
            params
            for method, params in client.requests
            if method == "thread/start" and "threadSource" in params
        ]
        self.assertEqual(4, len(worker_starts))
        self.assertTrue(
            all(params.get("model") == "gpt-default-project" for params in worker_starts)
        )
        self.assertTrue(
            all("reasoningEffort" not in params for params in worker_starts)
        )
        root_turn = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == client.root_thread_id
            and params.get("outputSchema") == SUPERVISOR_SCHEMA
        )
        planning_turn = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        )
        for params in (root_turn, planning_turn):
            self.assertEqual("gpt-default-project", params.get("model"))
            self.assertEqual("ultra", params.get("effort"))
            self.assertEqual("priority", params.get("serviceTier"))
        session.interrupt_turn()

    def test_explicit_project_effort_survives_thread_start_default(self) -> None:
        client = _ScriptedClient(
            block={"root", "planning"},
            response_model="gpt-5.6-sol",
            response_effort="low",
        )
        session = self._session(client)
        session.start_turn(
            "建一个木屋",
            effort="ultra",
            team_override="team",
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "planning"
                for method, params in client.requests
            ):
                break
            time.sleep(0.01)

        worker_starts = [
            params
            for method, params in client.requests
            if method == "thread/start" and "threadSource" in params
        ]
        self.assertEqual(4, len(worker_starts))
        self.assertTrue(
            all("reasoningEffort" not in params for params in worker_starts)
        )
        root_turn = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == client.root_thread_id
            and params.get("outputSchema") == SUPERVISOR_SCHEMA
        )
        planning_turn = next(
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        )
        self.assertEqual("ultra", root_turn.get("effort"))
        self.assertEqual("ultra", planning_turn.get("effort"))
        planning = next(
            item
            for item in session.project_team_snapshot()["projects"][0]["threads"]
            if item["role"] == "planning"
        )
        descriptor = session._project_threads.transfer_descriptor(
            planning["thread_id"]
        )
        self.assertEqual("ultra", descriptor["effort"])
        session.interrupt_turn()

    def test_project_guidance_steers_only_active_role_and_queues_idle_role(self) -> None:
        runtime_root = REPOSITORY_ROOT / ".runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=runtime_root)
        self.addCleanup(directory.cleanup)
        project_root = Path(directory.name)
        client = _ScriptedClient(block={"root", "planning", "visual_review"})
        session = self._session(client, project_root=project_root)

        session.start_turn("Build the referenced asset", team_override="team")
        project = session.project_team_snapshot()["projects"][0]
        root_thread_id = project["root_thread_id"]
        visual_thread_id = next(
            thread["thread_id"]
            for thread in project["threads"]
            if thread["role"] == "visual_review"
        )
        attachment_root = project_root / ".runtime" / "attachments"
        root_directory = attachment_root / root_thread_id
        visual_directory = attachment_root / visual_thread_id
        root_directory.mkdir(parents=True)
        visual_directory.mkdir(parents=True)
        root_image = root_directory / "root-reference.png"
        visual_image = visual_directory / "visual-reference.webp"
        root_image.write_bytes(b"\x89PNG\r\n\x1a\n")
        visual_image.write_bytes(b"RIFFxxxxWEBP")

        root_receipt = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=root_thread_id,
            text="Preserve this silhouette.",
            local_image_paths=[str(root_image)],
        )
        root_steer = next(
            params
            for method, params in reversed(client.requests)
            if method == "turn/steer" and params["threadId"] == root_thread_id
        )
        self.assertEqual(
            [
                {
                    "type": "text",
                    "text": "Preserve this silhouette.",
                    "text_elements": [],
                },
                {"type": "localImage", "path": str(root_image.resolve())},
            ],
            root_steer["input"],
        )
        self.assertTrue(root_receipt["guidance_accepted"])
        self.assertRegex(root_receipt["guidance_id"], r"^guidance-[0-9a-f]{32}$")
        self.assertEqual(
            {
                "project_id": project["project_id"],
                "thread_id": root_thread_id,
                "role": "supervisor",
            },
            root_receipt["guidance_target"],
        )
        self.assertEqual("steered", root_receipt["workflow_delivery"]["mode"])

        visual_turn_starts_before = sum(
            method == "turn/start" and params.get("threadId") == visual_thread_id
            for method, params in client.requests
        )
        visual_receipt = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=visual_thread_id,
            text="",
            local_image_paths=[str(visual_image)],
        )
        self.assertEqual(
            {"mode": "queued", "project_id": project["project_id"]},
            visual_receipt["workflow_delivery"],
        )
        self.assertEqual(
            visual_turn_starts_before,
            sum(
                method == "turn/start" and params.get("threadId") == visual_thread_id
                for method, params in client.requests
            ),
            "idle role guidance must never create a side Turn",
        )
        self.assertEqual(
            {
                "project_id": project["project_id"],
                "thread_id": visual_thread_id,
                "role": "visual_review",
            },
            visual_receipt["guidance_target"],
        )
        visual_thread_start = next(
            params
            for method, params in client.requests
            if method == "thread/start"
            and params.get("threadSource", "").endswith("/visual_review")
        )
        self.assertEqual(
            {**PROJECT_HIA_DISABLE_CONFIG, "multi_agent_mode": "proactive"},
            visual_thread_start["config"],
        )
        self.assertFalse(
            visual_thread_start["config"]["mcp_servers.hia_mcp_v2.enabled"]
        )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            queued = internal.get("_user_guidance", [])
            self.assertEqual("visual_review", queued[-1]["target_role"])
            self.assertEqual(visual_thread_id, queued[-1]["target_thread_id"])
            self.assertEqual(
                (str(visual_image.resolve()),),
                queued[-1]["local_image_paths"],
            )

        request_count = len(client.requests)
        with self.assertRaises(BridgeError) as crossed:
            session.append_project_guidance(
                project_id=project["project_id"],
                thread_id=visual_thread_id,
                text="This image belongs to another role.",
                local_image_paths=[str(root_image)],
            )
        self.assertEqual("INVALID_LOCAL_IMAGE_PATH", crossed.exception.code)
        self.assertEqual(request_count, len(client.requests))
        session.interrupt_turn()

    def test_active_project_delta_targets_supervisor_and_preserves_execution_lock(self) -> None:
        client = _ScriptedClient(block={"execution"})
        session = self._session(client)
        session.start_turn("建一个木屋", team_override="team")

        deadline = time.monotonic() + 3.0
        execution_turn = None
        while time.monotonic() < deadline:
            execution_turn = next(
                (
                    turn_id
                    for (thread_id, turn_id), role in client._active.items()
                    if role == "execution"
                ),
                None,
            )
            if execution_turn is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(execution_turn)
        project = session.project_team_snapshot()["projects"][0]
        root_thread_id = project["root_thread_id"]
        attachment_dir = (
            REPOSITORY_ROOT / ".runtime" / "attachments" / root_thread_id
        )
        attachment_dir.mkdir(parents=True, exist_ok=True)
        image = attachment_dir / "active-project-delta.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.addCleanup(image.unlink, missing_ok=True)
        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        capture = Path(capture_directory.name) / "current-stage.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\n")

        guided = session.append_active_supervisor_guidance(
            text="用户补充：门洞必须加宽并保留屋檐比例。",
            local_image_paths=[str(image)],
        )

        self.assertEqual(
            {
                "project_id": project["project_id"],
                "thread_id": root_thread_id,
                "role": "supervisor",
            },
            guided["guidance_target"],
        )
        self.assertTrue(guided["guidance_accepted"])
        self.assertRegex(guided["guidance_id"], r"^guidance-[0-9a-f]{32}$")
        self.assertEqual(
            {
                "mode": "steered",
                "project_id": project["project_id"],
                "thread_id": "thread-execution",
                "turn_id": execution_turn,
                "role": "execution",
            },
            guided["workflow_delivery"],
        )
        execution_steer = next(
            params
            for method, params in reversed(client.requests)
            if method == "turn/steer"
            and params["threadId"] == "thread-execution"
        )
        self.assertEqual(
            [
                {
                    "type": "text",
                    "text": "用户补充：门洞必须加宽并保留屋檐比例。",
                    "text_elements": [],
                },
                {"type": "localImage", "path": str(image.resolve())},
            ],
            execution_steer["input"],
        )
        self.assertEqual(
            1,
            sum(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            ),
        )
        self.assertIn(
            ("thread-execution", execution_turn),
            client._active,
        )
        synced_supervisor = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=root_thread_id,
            text="Synced Supervisor delta must reach the active Execution Turn.",
        )
        self.assertEqual(
            {
                "project_id": project["project_id"],
                "thread_id": root_thread_id,
                "role": "supervisor",
            },
            synced_supervisor["guidance_target"],
        )
        self.assertEqual(
            {
                "mode": "steered",
                "project_id": project["project_id"],
                "thread_id": "thread-execution",
                "turn_id": execution_turn,
                "role": "execution",
            },
            synced_supervisor["workflow_delivery"],
        )
        synced_steer = next(
            params
            for method, params in reversed(client.requests)
            if method == "turn/steer"
        )
        self.assertEqual("thread-execution", synced_steer["threadId"])
        self.assertEqual(
            "Synced Supervisor delta must reach the active Execution Turn.",
            synced_steer["input"][0]["text"],
        )
        with self.assertRaises(BridgeError) as mismatch:
            session.append_active_supervisor_guidance(
                project_id="project-unrelated",
                text="must not cross projects",
            )
        self.assertEqual("PROJECT_THREAD_PROJECT_MISMATCH", mismatch.exception.code)
        with self.assertRaises(BridgeError) as competing:
            session.start_turn("另一个普通场景写任务", team_override="team")
        self.assertEqual(
            "PROJECT_THREAD_GUIDANCE_REQUIRED", competing.exception.code
        )
        self.assertEqual(
            project["project_id"], competing.exception.details["project_id"]
        )
        self.assertEqual(
            "supervisor", competing.exception.details["project_role"]
        )

        client.execution = _execution([str(capture)])
        client.block.discard("execution")
        client._active.pop(("thread-execution", execution_turn), None)
        client._emit_completed(
            "thread-execution",
            execution_turn,
            _execution([str(capture)]),
            "completed",
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            project = session.project_team_snapshot()["projects"][0]
            if project["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual("completed", project["status"])
        review_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]]
            in {"visual_review", "technical_review"}
        ]
        self.assertGreaterEqual(len(review_starts), 2)
        self.assertTrue(
            all(
                "AUTHORITATIVE USER GUIDANCE ACCEPTED AFTER PROJECT START"
                in params["input"][0]["text"]
                and "门洞必须加宽" in params["input"][0]["text"]
                for params in review_starts
            )
        )
        visual_start = next(
            params
            for params in review_starts
            if client.roles[params["threadId"]] == "visual_review"
        )
        self.assertIn(
            {"type": "localImage", "path": str(image.resolve())},
            visual_start["input"],
        )
        supervisor_decisions = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and params["threadId"] == root_thread_id
            and "AUTHORITATIVE USER GUIDANCE ACCEPTED AFTER PROJECT START"
            in params["input"][0]["text"]
        ]
        self.assertTrue(supervisor_decisions)

    def test_guidance_after_execution_evidence_forces_full_final_stage_cycle(self) -> None:
        class _LateGuidanceClient(_ScriptedClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.execution_count = 0
                self.inject_late_guidance: Any = None

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if (
                    method == "turn/start"
                    and params.get("outputSchema") == EXECUTION_SCHEMA
                ):
                    self.execution_count += 1
                    if self.execution_count == 4 and callable(self.inject_late_guidance):
                        callback = self.inject_late_guidance
                        self.inject_late_guidance = None
                        callback()
                return result

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        original_capture = Path(capture_directory.name) / "before-late-guidance.png"
        repaired_capture = Path(capture_directory.name) / "after-late-guidance.png"
        original_capture.write_bytes(b"\x89PNG\r\n\x1a\nbefore")
        repaired_capture.write_bytes(b"\x89PNG\r\n\x1a\nafter")
        client = _LateGuidanceClient(
            execution_sequence=[
                *[
                    _execution([str(original_capture)])
                    for _index in range(4)
                ],
                _execution([str(repaired_capture)], evidence_suffix="-late"),
            ],
            visual_sequence=[
                *[_review("visual") for _index in range(4)],
                _review("visual", "-late"),
            ],
            technical_sequence=[
                *[_review("technical") for _index in range(4)],
                _review("technical", "-late"),
            ],
        )
        session = self._session(client)
        receipt: dict[str, Any] = {}

        def inject() -> None:
            project = session.project_team_snapshot()["projects"][0]
            receipt.update(
                session.append_project_guidance(
                    project_id=project["project_id"],
                    thread_id=project["root_thread_id"],
                    text="Late User delta must be repaired and reviewed before completion.",
                )
            )

        client.inject_late_guidance = inject
        session.start_turn("Build a complete wooden cabin", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual(
            {"mode": "queued", "project_id": project["project_id"]},
            receipt["workflow_delivery"],
        )
        execution_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "execution"
        ]
        self.assertEqual(5, len(execution_starts))
        visual_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "visual_review"
        ]
        technical_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "technical_review"
        ]
        self.assertEqual(5, len(visual_starts))
        self.assertEqual(5, len(technical_starts))
        self.assertIn(
            "Late User delta must be repaired and reviewed before completion.",
            execution_starts[-1]["input"][0]["text"],
        )
        for review_params in (visual_starts[-1], technical_starts[-1]):
            self.assertIn(
                "Late User delta must be repaired and reviewed before completion.",
                review_params["input"][0]["text"],
            )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(1, internal.get("_guidance_revision"))
            self.assertEqual(1, internal.get("_guidance_consumed_revision"))

    def test_planning_only_guidance_revises_plan_and_reauthorizes_before_execution(self) -> None:
        class _PlanningGuidanceClient(_ScriptedClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.execution_count = 0
                self.inject_guidance: Any = None

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if method == "turn/start" and params.get("outputSchema") == EXECUTION_SCHEMA:
                    self.execution_count += 1
                    if self.execution_count == 4 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return result

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        before = Path(capture_directory.name) / "planning-before.png"
        after = Path(capture_directory.name) / "planning-after.png"
        before.write_bytes(b"\x89PNG\r\n\x1a\nbefore")
        after.write_bytes(b"\x89PNG\r\n\x1a\nafter")
        client = _PlanningGuidanceClient(
            execution_sequence=[
                *[_execution([str(before)]) for _index in range(4)],
                _execution([str(after)], evidence_suffix="-planning"),
            ],
            visual_sequence=[
                *[_review("visual") for _index in range(4)],
                _review("visual", "-planning"),
            ],
            technical_sequence=[
                *[_review("technical") for _index in range(4)],
                _review("technical", "-planning"),
            ],
        )
        session = self._session(client)
        receipt: dict[str, Any] = {}
        guidance = "Planning must add an exact removable roof-service access panel."

        def inject() -> None:
            project = session.project_team_snapshot()["projects"][0]
            planning_thread = next(
                item
                for item in project["threads"]
                if item["role"] == "planning"
            )
            receipt.update(
                session.append_project_guidance(
                    project_id=project["project_id"],
                    thread_id=planning_thread["thread_id"],
                    text=guidance,
                )
            )

        client.inject_guidance = inject
        session.start_turn("Build a complete wooden cabin", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual("queued", receipt["workflow_delivery"]["mode"])
        planning_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        ]
        authorization_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "supervisor"
            and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        ]
        self.assertEqual(2, len(planning_starts))
        self.assertEqual(2, len(authorization_starts))
        self.assertIn(guidance, authorization_starts[-1]["input"][0]["text"])
        execution_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "execution"
        ]
        self.assertEqual(5, len(execution_starts))
        self.assertIn(guidance, execution_starts[-1]["input"][0]["text"])
        self.assertNotIn(
            "AUTHORITATIVE USER GUIDANCE ACCEPTED AFTER PROJECT START",
            execution_starts[-1]["input"][0]["text"],
        )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            role_revisions = internal.get("_role_guidance_revisions", {})
            self.assertEqual(1, role_revisions.get("planning"))
            self.assertEqual(0, role_revisions.get("execution", 0))

    def test_guidance_during_first_authorization_returns_to_planning_before_execution(
        self,
    ) -> None:
        class _AuthorizationRaceClient(_ScriptedClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.inject_guidance: Any = None
                self.authorization_count = 0

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if (
                    method == "turn/start"
                    and params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
                ):
                    self.authorization_count += 1
                    if self.authorization_count == 1 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return super().request(method, params)

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        capture = Path(capture_directory.name) / "authorization-race-stage.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\nauthorization-race")
        client = _AuthorizationRaceClient(
            execution_sequence=[_execution([str(capture)]) for _index in range(4)],
            visual_sequence=[_review("visual") for _index in range(4)],
            technical_sequence=[_review("technical") for _index in range(4)],
        )
        session = self._session(client)
        receipt: dict[str, Any] = {}
        guidance = "User requires a glazed roof monitor instead of a solid ridge cap."
        guidance_image: Path | None = None

        def inject() -> None:
            nonlocal guidance_image
            project = session.project_team_snapshot()["projects"][0]
            planning_thread = next(
                item for item in project["threads"] if item["role"] == "planning"
            )
            attachment_dir = (
                REPOSITORY_ROOT
                / ".runtime"
                / "attachments"
                / planning_thread["thread_id"]
            )
            attachment_dir.mkdir(parents=True, exist_ok=True)
            guidance_image = attachment_dir / "authorization-race-reference.png"
            guidance_image.write_bytes(b"\x89PNG\r\n\x1a\nguidance")
            self.addCleanup(guidance_image.unlink, missing_ok=True)
            receipt.update(
                session.append_project_guidance(
                    project_id=project["project_id"],
                    thread_id=planning_thread["thread_id"],
                    text=guidance,
                    local_image_paths=[str(guidance_image)],
                )
            )

        client.inject_guidance = inject
        session.start_turn("Build a complete wooden cabin", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual("queued", receipt["workflow_delivery"]["mode"])
        self.assertIsNotNone(guidance_image)
        turn_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
        ]
        first_authorization_index = next(
            index
            for index, params in enumerate(turn_starts)
            if params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        )
        next_start = turn_starts[first_authorization_index + 1]
        self.assertEqual("planning", client.roles[next_start["threadId"]])
        self.assertIn(guidance, next_start["input"][0]["text"])
        self.assertIn(
            str(guidance_image.resolve()),
            [
                item.get("path")
                for item in next_start["input"]
                if item.get("type") == "localImage"
            ],
        )
        second_authorization = next(
            params
            for params in turn_starts[first_authorization_index + 2 :]
            if params.get("outputSchema") == SUPERVISOR_PLAN_SCHEMA
        )
        self.assertIn(guidance, second_authorization["input"][0]["text"])
        first_execution_index = next(
            index
            for index, params in enumerate(turn_starts)
            if params.get("outputSchema") == EXECUTION_SCHEMA
        )
        self.assertGreater(first_execution_index, first_authorization_index + 2)
        self.assertIn(guidance, turn_starts[first_execution_index]["input"][0]["text"])

    def test_guidance_after_blocked_evidence_prevents_stale_terminal_block(self) -> None:
        class _BlockedRaceClient(_ScriptedClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.execution_count = 0
                self.inject_guidance: Any = None

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if method == "turn/start" and params.get("outputSchema") == EXECUTION_SCHEMA:
                    self.execution_count += 1
                    if self.execution_count == 1 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return result

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        capture = Path(capture_directory.name) / "unblocked-after-guidance.png"
        capture.write_bytes(b"\x89PNG\r\n\x1a\nunblocked")
        client = _BlockedRaceClient(
            execution_sequence=[
                _execution(outcome="blocked"),
                *[_execution([str(capture)]) for _index in range(4)],
            ]
        )
        session = self._session(client)
        receipt: dict[str, Any] = {}

        def inject() -> None:
            project = session.project_team_snapshot()["projects"][0]
            receipt.update(
                session.append_project_guidance(
                    project_id=project["project_id"],
                    thread_id=project["root_thread_id"],
                    text="Use the restored session and retry before declaring blocked.",
                )
            )

        client.inject_guidance = inject
        session.start_turn("Build a complete wooden cabin", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual(
            {"mode": "queued", "project_id": project["project_id"]},
            receipt["workflow_delivery"],
        )
        execution_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "execution"
        ]
        self.assertEqual(5, len(execution_starts))
        self.assertIn(
            "Use the restored session and retry before declaring blocked.",
            execution_starts[1]["input"][0]["text"],
        )
        self.assertEqual("complete", client.goals[project["root_thread_id"]]["status"])

    def test_guidance_after_review_block_decision_forces_fresh_evidence_cycle(self) -> None:
        proven_block = {
            "decision": "blocked",
            "summary": "The external Houdini session is unavailable.",
            "repair_card": "",
            "collaboration_mode": "serial-fallback",
            "external_dependency": {
                "proven": True,
                "required_external_change": "Restore the external Houdini service.",
                "observed_blocker": "Houdini service unavailable and session unreachable.",
                "why_codex_cannot_resolve": "Codex cannot restore the external Houdini service session.",
                "evidence_refs": ["tool-external-blocker"],
            },
        }

        class _ReviewBlockedRaceClient(_ScriptedClient):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.inject_guidance: Any = None
                self.decision_count = 0

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if (
                    method == "turn/start"
                    and params.get("outputSchema") == SUPERVISOR_DECISION_SCHEMA
                ):
                    self.decision_count += 1
                    if self.decision_count == 1 and callable(self.inject_guidance):
                        callback = self.inject_guidance
                        self.inject_guidance = None
                        callback()
                return result

            def _emit_completed(
                self,
                thread_id: str,
                turn_id: str,
                payload: Mapping[str, Any] | None,
                status: str,
            ) -> None:
                if (
                    self.roles.get(thread_id) == "execution"
                    and isinstance(payload, Mapping)
                    and payload.get("outcome") == "completed"
                    and self.decision_count == 0
                    and callable(self._sink)
                ):
                    self._sink(
                        {
                            "type": "codex_notification",
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": "tool-external-blocker",
                                    "type": "mcpToolCall",
                                    "tool": "hia_context",
                                    "status": "failed",
                                    "arguments": {"path": "/obj"},
                                    "result": {
                                        "structuredContent": {
                                            "ok": False,
                                            "error": {
                                                "code": "HIA_SESSION_UNAVAILABLE",
                                                "message": "Houdini service unavailable; session unreachable",
                                            },
                                        }
                                    },
                                },
                            },
                        }
                    )
                super()._emit_completed(thread_id, turn_id, payload, status)

        capture_root = REPOSITORY_ROOT / ".runtime" / "cache" / "screenshots"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_directory = tempfile.TemporaryDirectory(dir=capture_root)
        self.addCleanup(capture_directory.cleanup)
        first_capture = Path(capture_directory.name) / "pre-guidance-review.png"
        repaired_capture = Path(capture_directory.name) / "post-guidance-review.png"
        first_capture.write_bytes(b"\x89PNG\r\n\x1a\npre")
        repaired_capture.write_bytes(b"\x89PNG\r\n\x1a\npost")
        client = _ReviewBlockedRaceClient(
            execution_sequence=[
                _execution([str(first_capture)]),
                _execution([str(repaired_capture)], evidence_suffix="-review-guidance"),
                *[_execution([str(first_capture)]) for _index in range(3)],
            ],
            visual_sequence=[
                _review("visual"),
                _review("visual", "-review-guidance"),
                *[_review("visual") for _index in range(3)],
            ],
            technical_sequence=[
                _review("technical"),
                _review("technical", "-review-guidance"),
                *[_review("technical") for _index in range(3)],
            ],
            decision_sequence=[proven_block],
        )
        session = self._session(client)
        receipt: dict[str, Any] = {}

        def inject() -> None:
            project = session.project_team_snapshot()["projects"][0]
            receipt.update(
                session.append_project_guidance(
                    project_id=project["project_id"],
                    thread_id=project["root_thread_id"],
                    text="Recheck the restored session before accepting a blocked verdict.",
                )
            )

        client.inject_guidance = inject
        session.start_turn("Build a complete wooden cabin", team_override="team")
        deadline = time.monotonic() + 5.0
        project = session.project_team_snapshot()["projects"][0]
        while time.monotonic() < deadline and project["status"] == "running":
            time.sleep(0.01)
            project = session.project_team_snapshot()["projects"][0]

        self.assertEqual("completed", project["status"])
        self.assertTrue(receipt["guidance_accepted"])
        execution_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "execution"
        ]
        self.assertEqual(5, len(execution_starts))
        self.assertIn(
            "Recheck the restored session before accepting a blocked verdict.",
            execution_starts[1]["input"][0]["text"],
        )
        self.assertEqual("complete", client.goals[project["root_thread_id"]]["status"])

    def test_project_guidance_keeps_capsule_when_steer_ack_mismatches(self) -> None:
        class _MismatchedSteerClient(_ScriptedClient):
            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                result = super().request(method, params)
                if method == "turn/steer":
                    return {"turnId": "turn-unrelated"}
                return result

        runtime_root = REPOSITORY_ROOT / ".runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=runtime_root)
        self.addCleanup(directory.cleanup)
        project_root = Path(directory.name)
        client = _MismatchedSteerClient(block={"root", "planning"})
        session = self._session(client, project_root=project_root)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]
        root_thread_id = project["root_thread_id"]
        root_directory = (
            project_root / ".runtime" / "attachments" / root_thread_id
        )
        root_directory.mkdir(parents=True)
        image = root_directory / "reference.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        receipt = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=root_thread_id,
            text="Keep the reference visible.",
            local_image_paths=[str(image)],
        )

        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual("queued", receipt["workflow_delivery"]["mode"])
        self.assertEqual(
            "PROJECT_THREAD_TURN_MISMATCH",
            receipt["workflow_delivery"]["delivery_warning"]["code"],
        )
        self.assertEqual(
            "running",
            session.project_team_snapshot()["projects"][0]["status"],
        )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(1, len(internal.get("_user_guidance", [])))
            self.assertEqual(1, internal.get("_guidance_revision"))

    def test_project_guidance_timeout_keeps_capsule_and_pending_settings(self) -> None:
        class _TimedOutSteerClient(_ScriptedClient):
            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "turn/steer":
                    super().request(method, params)
                    raise BridgeError(
                        "CODEX_REQUEST_TIMEOUT",
                        "Codex request timed out after remote delivery: turn/steer",
                        http_status=504,
                        details={"method": "turn/steer"},
                    )
                return super().request(method, params)

        client = _TimedOutSteerClient(block={"root", "planning"})
        session = self._session(client)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]

        receipt = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=project["root_thread_id"],
            text="Do not clear this unacknowledged guidance.",
            model="gpt-guidance-only",
            effort="high",
            service_tier="priority",
        )

        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual("queued", receipt["workflow_delivery"]["mode"])
        self.assertEqual(
            "CODEX_REQUEST_TIMEOUT",
            receipt["workflow_delivery"]["delivery_warning"]["code"],
        )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(1, len(internal.get("_user_guidance", [])))
            self.assertEqual(1, internal.get("_guidance_revision"))
            supervisor = session._project_threads._thread(internal, "supervisor")
            self.assertEqual("gpt-guidance-only", supervisor.get("_next_model"))
            self.assertEqual("high", supervisor.get("_next_effort"))
            self.assertEqual("priority", supervisor.get("_next_service_tier"))

    def test_no_active_guidance_is_accepted_queued_and_manual_retry_keeps_fact(self) -> None:
        class _NoActiveOnceClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"root", "planning"})
                self.no_active_once = True

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "turn/steer" and self.no_active_once:
                    self.no_active_once = False
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                    raise BridgeError(
                        "NO_ACTIVE_TURN",
                        "The selected Turn ended before steer delivery",
                        http_status=409,
                    )
                return super().request(method, params)

        client = _NoActiveOnceClient()
        session = self._session(client)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]
        attachment_dir = (
            REPOSITORY_ROOT
            / ".runtime"
            / "attachments"
            / project["root_thread_id"]
        )
        attachment_dir.mkdir(parents=True, exist_ok=True)
        image = attachment_dir / "turn-ended-guidance.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.addCleanup(image.unlink, missing_ok=True)
        request = {
            "project_id": project["project_id"],
            "thread_id": project["root_thread_id"],
            "text": "Keep this exact User fact after the active Turn ends.",
            "local_image_paths": [str(image)],
        }

        first = session.append_project_guidance(**request)
        second = session.append_project_guidance(**request)

        self.assertTrue(first["guidance_accepted"])
        self.assertEqual("queued", first["workflow_delivery"]["mode"])
        self.assertEqual(
            "NO_ACTIVE_TURN",
            first["workflow_delivery"]["delivery_warning"]["code"],
        )
        self.assertTrue(second["guidance_accepted"])
        self.assertEqual("queued", second["workflow_delivery"]["mode"])
        self.assertEqual(1, len([m for m, _ in client.requests if m == "turn/steer"]))
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(2, len(internal.get("_user_guidance", [])))
            self.assertEqual(2, internal.get("_guidance_revision"))
        prompt, guidance_images, revision = session._project_threads._with_user_guidance(
            project["project_id"],
            "execution",
            "BASE",
            (),
        )
        self.assertEqual(2, revision)
        self.assertEqual(2, prompt.count(request["text"]))
        self.assertEqual((str(image.resolve()),), guidance_images)

    def test_guidance_accepts_more_than_sixteen_and_uses_utf8_byte_budget(self) -> None:
        client = _ScriptedClient(block={"root", "planning"})
        session = self._session(client)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]
        visual = next(
            thread
            for thread in project["threads"]
            if thread["role"] == "visual_review"
        )

        receipts = [
            session.append_project_guidance(
                project_id=project["project_id"],
                thread_id=visual["thread_id"],
                text=f"Visual guidance fact {index:02d} must remain exact.",
            )
            for index in range(20)
        ]

        self.assertTrue(all(item["guidance_accepted"] for item in receipts))
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(20, len(internal.get("_user_guidance", [])))
            self.assertEqual(20, internal.get("_guidance_revision"))
        prompt, _images, revision = session._project_threads._with_user_guidance(
            project["project_id"],
            "visual_review",
            "BASE",
            (),
        )
        self.assertEqual(20, revision)
        for index in range(20):
            self.assertIn(f"Visual guidance fact {index:02d}", prompt)
        self.assertLess(len(prompt.encode("utf-8")), 1_048_576)

        # A separate project proves the limit follows actual UTF-8 handoff bytes.
        budget_client = _ScriptedClient(block={"root", "planning"})
        budget_session = self._session(budget_client)
        budget_session.start_turn("Build another asset", team_override="team")
        self.addCleanup(budget_session.interrupt_turn)
        budget_project = budget_session.project_team_snapshot()["projects"][0]
        budget_visual = next(
            thread
            for thread in budget_project["threads"]
            if thread["role"] == "visual_review"
        )
        first = budget_session.append_project_guidance(
            project_id=budget_project["project_id"],
            thread_id=budget_visual["thread_id"],
            text="x" * 65_536,
        )
        second = budget_session.append_project_guidance(
            project_id=budget_project["project_id"],
            thread_id=budget_visual["thread_id"],
            text="y" * 65_536,
        )
        self.assertTrue(first["guidance_accepted"] and second["guidance_accepted"])
        with self.assertRaises(BridgeError) as full:
            budget_session.append_project_guidance(
                project_id=budget_project["project_id"],
                thread_id=budget_visual["thread_id"],
                text="z",
            )
        self.assertEqual("PROJECT_GUIDANCE_QUEUE_FULL", full.exception.code)

    def test_guidance_image_budget_is_per_role_unique_and_rejects_before_mutation(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        execution_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "execution"
        )

        paths = [f"E:/guidance/execution-{index:02d}.png" for index in range(17)]
        for offset in range(0, 16, 4):
            receipt = coordinator.append_guidance(
                project["project_id"],
                execution_thread,
                "",
                local_image_paths=tuple(paths[offset : offset + 4]),
            )
            self.assertTrue(receipt["guidance_accepted"])

        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            revision_before = internal["_guidance_revision"]
            capsules_before = len(internal["_user_guidance"])
        with self.assertRaises(BridgeError) as full:
            coordinator.append_guidance(
                project["project_id"],
                execution_thread,
                "",
                local_image_paths=(paths[16],),
            )
        self.assertEqual("PROJECT_GUIDANCE_IMAGE_QUEUE_FULL", full.exception.code)
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(revision_before, internal["_guidance_revision"])
            self.assertEqual(capsules_before, len(internal["_user_guidance"]))
        _prompt, images, revision = coordinator._with_user_guidance(
            project["project_id"], "execution", "BASE", ()
        )
        self.assertEqual(4, revision)
        self.assertEqual(16, len(images))
        self.assertEqual(16, len(set(images)))

    def test_early_image_after_full_root_input_is_queued_not_overflow_steered(
        self,
    ) -> None:
        client = _ScriptedClient()
        base_images = tuple(
            f"E:/references/root-{index:02d}.png" for index in range(16)
        )
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project(
            "thread-root",
            "build a cabin",
            local_image_paths=base_images,
        )
        self.addCleanup(coordinator.interrupt_project)
        with coordinator._condition:
            pending = coordinator._pending["thread-root"]
            pending.turn_id = "turn-root"

        receipt = coordinator.accept_supervisor_guidance(
            project["project_id"],
            "Use this additional elevation reference too.",
            local_image_paths=("E:/references/root-extra.png",),
        )

        self.assertTrue(receipt["guidance_accepted"])
        self.assertEqual("queued", receipt["workflow_delivery"]["mode"])
        later_text = coordinator.accept_supervisor_guidance(
            project["project_id"],
            "Keep the additional elevation image authoritative.",
        )
        self.assertTrue(later_text["guidance_accepted"])
        self.assertEqual("queued", later_text["workflow_delivery"]["mode"])
        self.assertFalse(any(method == "turn/steer" for method, _ in client.requests))
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(2, internal["_guidance_revision"])
            self.assertEqual(2, len(internal["_user_guidance"]))
            self.assertEqual(0, coordinator._pending["thread-root"].guidance_revision)

    def test_initial_reference_images_and_early_extra_are_ingested_in_bounded_turns(
        self,
    ) -> None:
        client = _ScriptedClient()
        base_images = tuple(
            f"E:/references/base-{index:02d}.png" for index in range(16)
        )
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project(
            "thread-root",
            "build a cabin",
            local_image_paths=base_images,
        )
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        planning_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "planning"
        )

        seventeenth = "E:/references/seventeenth.png"
        accepted_early = coordinator.append_guidance(
            project["project_id"],
            planning_thread,
            "",
            local_image_paths=(seventeenth,),
        )
        self.assertTrue(accepted_early["guidance_accepted"])
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(1, internal["_guidance_revision"])
            self.assertEqual(1, len(internal["_user_guidance"]))

        coordinator._run_structured_turn(
            project["project_id"],
            "planning",
            "Return the complete project blueprint.",
            PLANNING_SCHEMA,
            images=base_images,
        )
        planning_starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "planning"
        ]
        self.assertEqual(2, len(planning_starts))
        routed_batches = [
            tuple(
                item["path"]
                for item in params["input"]
                if item.get("type") == "localImage"
            )
            for params in planning_starts
        ]
        self.assertEqual(base_images, routed_batches[0])
        self.assertEqual((seventeenth,), routed_batches[1])
        self.assertIn("REFERENCE INTAKE ONLY", planning_starts[0]["input"][0]["text"])
        self.assertNotIn("REFERENCE INTAKE ONLY", planning_starts[1]["input"][0]["text"])
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertTrue(
                coordinator._thread(internal, "planning").get(
                    "_base_images_sent"
                )
            )

        next_images = tuple(
            f"E:/references/guidance-{index:02d}.png" for index in range(16)
        )
        accepted = coordinator.append_guidance(
            project["project_id"],
            planning_thread,
            "",
            local_image_paths=next_images,
        )
        self.assertTrue(accepted["guidance_accepted"])
        _prompt, routed, _revision = coordinator._with_user_guidance(
            project["project_id"], "planning", "BASE", base_images
        )
        self.assertEqual(next_images, routed)
        self.assertEqual(16, len(routed))

    def test_visual_review_splits_full_guidance_and_capture_batches_without_loss(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        visual_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "visual_review"
        )
        shared = "E:/reviews/shared-reference-and-capture.png"
        guidance_images = (
            shared,
            *(f"E:/guidance/visual-{index:02d}.png" for index in range(15)),
        )
        review_images = (
            shared,
            *(f"E:/reviews/current-{index:02d}.png" for index in range(15)),
        )
        accepted = coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=guidance_images,
        )
        self.assertTrue(accepted["guidance_accepted"])
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            revision_before = internal["_guidance_revision"]
            capsules_before = len(internal["_user_guidance"])
        seventeenth = "E:/guidance/visual-seventeenth.png"
        accepted_more = coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=(seventeenth,),
        )
        self.assertTrue(accepted_more["guidance_accepted"])
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(revision_before + 1, internal["_guidance_revision"])
            self.assertEqual(capsules_before + 1, len(internal["_user_guidance"]))

        coordinator._run_structured_turn(
            project["project_id"],
            "visual_review",
            "Review the current stage against every User reference.",
            REVIEW_SCHEMA,
            images=review_images,
        )
        starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "visual_review"
        ]
        self.assertEqual(3, len(starts))
        routed_batches = [
            [
                item["path"]
                for item in params["input"]
                if item.get("type") == "localImage"
            ]
            for params in starts
        ]
        self.assertEqual(guidance_images, tuple(routed_batches[0]))
        self.assertEqual((seventeenth,), tuple(routed_batches[1]))
        self.assertEqual(review_images, tuple(routed_batches[2]))
        self.assertTrue(all(len(batch) == len(set(batch)) <= 16 for batch in routed_batches))
        self.assertIn("REFERENCE INTAKE ONLY", starts[0]["input"][0]["text"])
        self.assertIn("REFERENCE INTAKE ONLY", starts[1]["input"][0]["text"])
        self.assertNotIn("REFERENCE INTAKE ONLY", starts[2]["input"][0]["text"])

    def test_all_read_only_roles_use_role_neutral_bounded_reference_intake(
        self,
    ) -> None:
        schemas = {
            "supervisor": SUPERVISOR_SCHEMA,
            "planning": PLANNING_SCHEMA,
            "visual_review": REVIEW_SCHEMA,
            "technical_review": REVIEW_SCHEMA,
        }
        for role, schema in schemas.items():
            with self.subTest(role=role):
                client = _ScriptedClient()
                first = tuple(
                    f"E:/guidance/{role}-a-{index:02d}.png"
                    for index in range(16)
                )
                last = f"E:/guidance/{role}-b.png"
                coordinator = ProjectThreadCoordinator(
                    REPOSITORY_ROOT, client, EventBuffer()
                )
                client.set_event_sink(coordinator.handle_client_event)
                project = coordinator.prepare_project(
                    "thread-root",
                    f"bounded {role} intake",
                    local_image_paths=first if role == "supervisor" else (),
                )
                self.addCleanup(coordinator.interrupt_project)
                snapshot = coordinator.snapshot(
                    mode="team", writable=True, settings_state_status="memory"
                )["projects"][0]
                thread_id = next(
                    item["thread_id"]
                    for item in snapshot["threads"]
                    if item["role"] == role
                )
                if role == "supervisor":
                    with coordinator._condition:
                        coordinator._pending.pop("thread-root", None)
                else:
                    coordinator.append_guidance(
                        project["project_id"],
                        thread_id,
                        "",
                        local_image_paths=first,
                    )
                coordinator.append_guidance(
                    project["project_id"],
                    thread_id,
                    "",
                    local_image_paths=(last,),
                )

                coordinator._run_structured_turn(
                    project["project_id"],
                    role,
                    "Return the formal role response.",
                    schema,
                    images=first if role == "supervisor" else (),
                )

                starts = [
                    params
                    for method, params in client.requests
                    if method == "turn/start" and params["threadId"] == thread_id
                ]
                self.assertEqual(2, len(starts))
                batches = [
                    tuple(
                        item["path"]
                        for item in params["input"]
                        if item.get("type") == "localImage"
                    )
                    for params in starts
                ]
                self.assertEqual(first, batches[0])
                self.assertEqual((last,), batches[1])
                intake_text = starts[0]["input"][0]["text"]
                self.assertIn("REFERENCE INTAKE ONLY", intake_text)
                self.assertIn("schema-valid provisional response", intake_text)
                self.assertNotIn("provisional repair review", intake_text)
                self.assertNotIn("Visual Review Thread", intake_text)
                self.assertNotIn(
                    "REFERENCE INTAKE ONLY", starts[1]["input"][0]["text"]
                )

    def test_invalid_prefetch_output_does_not_consume_undelivered_revision(
        self,
    ) -> None:
        class _InvalidFirstVisualClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__()
                self.invalid_visual_pending = True

            def _emit_completed(
                self,
                thread_id: str,
                turn_id: str,
                payload: Mapping[str, Any] | None,
                status: str,
            ) -> None:
                if (
                    self.invalid_visual_pending
                    and self.roles.get(thread_id) == "visual_review"
                ):
                    self.invalid_visual_pending = False
                    sink = self._sink
                    assert callable(sink)
                    sink(
                        {
                            "type": "codex_notification",
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {"id": turn_id, "status": "inProgress"},
                            },
                        }
                    )
                    sink(
                        {
                            "type": "codex_notification",
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": "invalid-message-" + turn_id,
                                    "type": "agentMessage",
                                    "text": "{not-json",
                                },
                            },
                        }
                    )
                    sink(
                        {
                            "type": "codex_notification",
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {"id": turn_id, "status": status},
                            },
                        }
                    )
                    return
                super()._emit_completed(thread_id, turn_id, payload, status)

        client = _InvalidFirstVisualClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project("thread-root", "visual intake retry")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        visual_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "visual_review"
        )
        first = tuple(
            f"E:/guidance/retry-a-{index:02d}.png" for index in range(16)
        )
        last = "E:/guidance/retry-b.png"
        coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=first,
        )
        coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=(last,),
        )

        coordinator._run_structured_turn(
            project["project_id"],
            "visual_review",
            "Return the formal visual review.",
            REVIEW_SCHEMA,
        )

        starts = [
            params
            for method, params in client.requests
            if method == "turn/start" and params["threadId"] == visual_thread
        ]
        self.assertEqual(3, len(starts))
        batches = [
            tuple(
                item["path"]
                for item in params["input"]
                if item.get("type") == "localImage"
            )
            for params in starts
        ]
        self.assertEqual(first, batches[0])
        self.assertEqual(first, batches[1])
        self.assertEqual((last,), batches[2])
        self.assertIn(
            "REFERENCE INTAKE OUTPUT CORRECTION ONLY",
            starts[1]["input"][0]["text"],
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(
                2,
                internal["_role_guidance_revisions"]["visual_review"],
            )
            self.assertEqual([], internal["_user_guidance"])

    def test_visual_review_duplicate_guidance_and_capture_share_one_slot(self) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        visual_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "visual_review"
        )
        shared = "E:/reviews/same-path.png"
        coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=(shared,),
        )
        review_images = (
            shared,
            *(f"E:/reviews/angle-{index:02d}.png" for index in range(15)),
        )
        coordinator._run_structured_turn(
            project["project_id"],
            "visual_review",
            "Review all current captured angles.",
            REVIEW_SCHEMA,
            images=review_images,
        )
        starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "visual_review"
        ]
        self.assertEqual(1, len(starts))
        routed = [
            item["path"]
            for item in starts[0]["input"]
            if item.get("type") == "localImage"
        ]
        self.assertEqual(16, len(routed))
        self.assertEqual(16, len(set(routed)))
        self.assertEqual(1, routed.count(shared))

    def test_visual_review_ingests_original_refs_and_guidance_once_before_captures(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        client.set_event_sink(coordinator.handle_client_event)
        base_refs = tuple(
            f"E:/references/user-{index:02d}.png" for index in range(8)
        )
        project = coordinator.prepare_project(
            "thread-root",
            "build a cabin from the references",
            local_image_paths=base_refs,
        )
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        visual_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "visual_review"
        )
        guidance_refs = tuple(
            f"E:/guidance/user-{index:02d}.png" for index in range(8)
        )
        coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=guidance_refs,
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            revision_before = internal["_guidance_revision"]
        ninth_guidance = "E:/guidance/user-ninth.png"
        accepted_ninth = coordinator.append_guidance(
            project["project_id"],
            visual_thread,
            "",
            local_image_paths=(ninth_guidance,),
        )
        self.assertTrue(accepted_ninth["guidance_accepted"])
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(revision_before + 1, internal["_guidance_revision"])

        first_stage_captures = tuple(
            f"E:/reviews/stage-one-{index:02d}.png" for index in range(16)
        )
        coordinator._run_structured_turn(
            project["project_id"],
            "visual_review",
            "Compare the current stage against all User references.",
            REVIEW_SCHEMA,
            images=first_stage_captures,
        )
        second_stage_captures = (
            "E:/reviews/stage-two-first.png",
            "E:/reviews/stage-two-last.png",
        )
        coordinator._run_structured_turn(
            project["project_id"],
            "visual_review",
            "Review the next current stage against retained references.",
            REVIEW_SCHEMA,
            images=second_stage_captures,
        )
        starts = [
            params
            for method, params in client.requests
            if method == "turn/start"
            and client.roles[params["threadId"]] == "visual_review"
        ]
        self.assertEqual(4, len(starts))
        batches = [
            tuple(
                item["path"]
                for item in params["input"]
                if item.get("type") == "localImage"
            )
            for params in starts
        ]
        self.assertEqual((*base_refs, *guidance_refs), batches[0])
        self.assertEqual((ninth_guidance,), batches[1])
        self.assertEqual(first_stage_captures, batches[2])
        self.assertEqual(second_stage_captures, batches[3])
        self.assertIn("REFERENCE INTAKE ONLY", starts[0]["input"][0]["text"])
        self.assertTrue(
            all(
                path not in batches[2] and path not in batches[3]
                for path in (*base_refs, *guidance_refs)
            )
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            visual = coordinator._thread(internal, "visual_review")
            self.assertTrue(visual.get("_base_images_sent"))
            self.assertEqual([], internal["_user_guidance"])

    def test_global_guidance_image_budget_respects_each_roles_consumed_revision(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        global_images = tuple(
            f"E:/guidance/global-{index:02d}.png" for index in range(14)
        )
        coordinator.accept_supervisor_guidance(
            project["project_id"], "", local_image_paths=global_images
        )
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        planning_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "planning"
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            internal["_role_guidance_revisions"]["planning"] = 1
            coordinator._release_consumed_guidance_images_locked(internal)

        planning_images = tuple(
            f"E:/guidance/planning-{index:02d}.png" for index in range(16)
        )
        coordinator.append_guidance(
            project["project_id"],
            planning_thread,
            "",
            local_image_paths=planning_images,
        )
        global_extra = "E:/guidance/global-overflow.png"
        accepted_global = coordinator.accept_supervisor_guidance(
            project["project_id"],
            "",
            local_image_paths=(global_extra,),
        )
        self.assertTrue(accepted_global["guidance_accepted"])
        _planning_prompt, routed_planning, _revision = (
            coordinator._with_user_guidance(
                project["project_id"], "planning", "BASE", ()
            )
        )
        _execution_prompt, routed_execution, _revision = (
            coordinator._with_user_guidance(
                project["project_id"], "execution", "BASE", ()
            )
        )
        self.assertEqual(planning_images, routed_planning)
        self.assertEqual((*global_images, global_extra), routed_execution)
        self.assertLessEqual(len(routed_planning), 16)
        self.assertLessEqual(len(routed_execution), 16)

    def test_consumed_image_guidance_releases_budget_without_deleting_files(
        self,
    ) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        technical_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "technical_review"
        )
        first_images = tuple(
            f"E:/guidance/technical-a-{index:02d}.png" for index in range(16)
        )
        coordinator.append_guidance(
            project["project_id"],
            technical_thread,
            "",
            local_image_paths=first_images,
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            internal["_role_guidance_revisions"]["technical_review"] = 1
            coordinator._release_consumed_guidance_images_locked(internal)
            self.assertEqual([], internal["_user_guidance"])

        second_images = tuple(
            f"E:/guidance/technical-b-{index:02d}.png" for index in range(16)
        )
        coordinator.append_guidance(
            project["project_id"],
            technical_thread,
            "",
            local_image_paths=second_images,
        )
        _prompt, routed, revision = coordinator._with_user_guidance(
            project["project_id"], "technical_review", "BASE", ()
        )
        self.assertEqual(2, revision)
        self.assertEqual(second_images, routed)
        self.assertLessEqual(len(routed), 16)

    def test_duplicate_image_only_guidance_does_not_grow_capsules(self) -> None:
        client = _ScriptedClient()
        coordinator = ProjectThreadCoordinator(REPOSITORY_ROOT, client, EventBuffer())
        project = coordinator.prepare_project("thread-root", "build a cabin")
        self.addCleanup(coordinator.interrupt_project)
        snapshot = coordinator.snapshot(
            mode="team", writable=True, settings_state_status="memory"
        )["projects"][0]
        execution_thread = next(
            item["thread_id"]
            for item in snapshot["threads"]
            if item["role"] == "execution"
        )
        path = "E:/guidance/same-reference.png"
        coordinator.append_guidance(
            project["project_id"],
            execution_thread,
            "",
            local_image_paths=(path,),
        )
        with self.assertRaises(BridgeError) as duplicate:
            coordinator.append_guidance(
                project["project_id"],
                execution_thread,
                "",
                local_image_paths=(path,),
            )
        self.assertEqual(
            "PROJECT_GUIDANCE_DUPLICATE_IMAGES",
            duplicate.exception.code,
        )
        with coordinator._condition:
            internal = coordinator._project(project["project_id"])
            self.assertEqual(1, internal["_guidance_revision"])
            self.assertEqual(1, len(internal["_user_guidance"]))

    def test_role_targeted_guidance_does_not_contaminate_unrelated_roles(self) -> None:
        client = _ScriptedClient(block={"root", "planning"})
        session = self._session(client)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]
        roles = {item["role"]: item["thread_id"] for item in project["threads"]}
        session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=roles["supervisor"],
            text="Project-wide User fact.",
        )
        session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=roles["visual_review"],
            text="Visual-only User fact.",
        )
        session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=roles["planning"],
            text="Planning-only User fact.",
        )

        routed = {
            role: session._project_threads._with_user_guidance(
                project["project_id"], role, "BASE", ()
            )
            for role in ("planning", "execution", "visual_review", "technical_review")
        }
        prompts = {role: value[0] for role, value in routed.items()}
        self.assertEqual(3, routed["planning"][2])
        self.assertEqual(1, routed["execution"][2])
        self.assertEqual(2, routed["visual_review"][2])
        self.assertEqual(1, routed["technical_review"][2])
        self.assertTrue(
            all("Project-wide User fact." in prompt for prompt in prompts.values())
        )
        self.assertIn("Visual-only User fact.", prompts["visual_review"])
        self.assertNotIn("Visual-only User fact.", prompts["execution"])
        self.assertNotIn("Visual-only User fact.", prompts["technical_review"])
        self.assertNotIn("Visual-only User fact.", prompts["planning"])
        self.assertIn("Planning-only User fact.", prompts["planning"])
        self.assertNotIn("Planning-only User fact.", prompts["execution"])
        self.assertNotIn("Planning-only User fact.", prompts["visual_review"])
        self.assertNotIn("Planning-only User fact.", prompts["technical_review"])
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(
                [1, 2, 3],
                [item["revision"] for item in internal["_user_guidance"]],
            )
            self.assertTrue(
                session._project_threads._guidance_guard_changed_locked(
                    internal,
                    expected_guidance_revision=None,
                    expected_guidance_revisions={
                        "execution": 1,
                        "visual_review": 1,
                    },
                )
            )

    def test_concurrent_guidance_is_rejected_before_mutation_and_slot_releases(self) -> None:
        class _BlockingSteerClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"root", "planning"})
                self.steer_entered = threading.Event()
                self.release_steer = threading.Event()
                self.steer_count = 0

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                if method == "turn/steer":
                    with self._lock:
                        self.requests.append((method, copy.deepcopy(dict(params))))
                        self.steer_count += 1
                        call_number = self.steer_count
                    if call_number == 1:
                        self.steer_entered.set()
                        if not self.release_steer.wait(2.0):
                            raise AssertionError("test did not release blocked steer")
                        raise BridgeError(
                            "CODEX_REQUEST_TIMEOUT",
                            "Codex request timed out: turn/steer",
                            http_status=504,
                        )
                    return {"turnId": params["expectedTurnId"]}
                return super().request(method, params)

        client = _BlockingSteerClient()
        session = self._session(client)
        session.start_turn("Build the referenced asset", team_override="team")
        self.addCleanup(session.interrupt_turn)
        project = session.project_team_snapshot()["projects"][0]
        receipts: list[dict[str, Any]] = []

        def first_request() -> None:
            try:
                receipts.append(
                    session.append_project_guidance(
                        project_id=project["project_id"],
                        thread_id=project["root_thread_id"],
                        text="First guidance will time out.",
                        model="first-model",
                    )
                )
            except Exception as exc:  # captured as a deterministic test failure
                receipts.append({"unexpected_error": exc})

        worker = threading.Thread(target=first_request)
        worker.start()
        self.assertTrue(client.steer_entered.wait(1.0))
        with self.assertRaises(BridgeError) as concurrent:
            session.append_project_guidance(
                project_id=project["project_id"],
                thread_id=project["root_thread_id"],
                text="Second guidance must not mutate anything.",
                model="second-model",
            )
        self.assertEqual("PROJECT_GUIDANCE_ACTIVE", concurrent.exception.code)
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(1, len(internal.get("_user_guidance", [])))
            self.assertEqual(1, internal.get("_guidance_revision"))
            supervisor = session._project_threads._thread(internal, "supervisor")
            self.assertEqual("first-model", supervisor.get("_next_model"))

        client.release_steer.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(receipts))
        self.assertNotIn("unexpected_error", receipts[0])
        self.assertTrue(receipts[0]["guidance_accepted"])
        self.assertEqual("queued", receipts[0]["workflow_delivery"]["mode"])
        self.assertEqual(
            "CODEX_REQUEST_TIMEOUT",
            receipts[0]["workflow_delivery"]["delivery_warning"]["code"],
        )
        with session._project_threads._condition:
            internal = session._project_threads._project(project["project_id"])
            self.assertEqual(1, len(internal.get("_user_guidance", [])))
            self.assertEqual(1, internal.get("_guidance_revision"))
            supervisor = session._project_threads._thread(internal, "supervisor")
            self.assertEqual("first-model", supervisor.get("_next_model"))
            self.assertNotIn(project["project_id"], session._project_threads._guidance_inflight)

        accepted = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=project["root_thread_id"],
            text="Third guidance succeeds after the failed slot releases.",
        )
        self.assertTrue(accepted["guidance_accepted"])
        self.assertEqual("queued", accepted["workflow_delivery"]["mode"])
        self.assertEqual(1, client.steer_count)

    def test_observed_root_turn_survives_ack_timeout_and_duplicate_submit(self) -> None:
        class _RootAckTimeoutClient(_ScriptedClient):
            def __init__(self) -> None:
                super().__init__(block={"root", "planning"})
                self.rejected_root_ack = False

            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                root_start = (
                    method == "turn/start"
                    and params.get("threadId") == self.root_thread_id
                    and params.get("outputSchema") == SUPERVISOR_SCHEMA
                )
                result = super().request(method, params)
                if root_start and not self.rejected_root_ack:
                    self.rejected_root_ack = True
                    turn_id = result["turn"]["id"]
                    assert callable(self._sink)
                    self._sink(
                        {
                            "type": "codex_notification",
                            "method": "turn/started",
                            "params": {
                                "threadId": self.root_thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "inProgress",
                                },
                            },
                        }
                    )
                    raise BridgeError(
                        "CODEX_REQUEST_TIMEOUT",
                        "Codex request timed out: turn/start",
                        http_status=504,
                        details={"method": "turn/start"},
                    )
                return result

        client = _RootAckTimeoutClient()
        session = self._session(client)

        with self.assertRaises(BridgeError) as timed_out:
            session.start_turn("build a project", team_override="team")

        self.assertEqual("CODEX_REQUEST_TIMEOUT", timed_out.exception.code)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "planning"
                for method, params in client.requests
            ):
                break
            time.sleep(0.01)

        project_team = session.project_team_snapshot()
        project = project_team["projects"][0]
        self.assertEqual("running", project["status"])
        self.assertEqual(5, len(project["threads"]))
        self.assertTrue(session.snapshot()["turn_active"])
        self.assertEqual("inProgress", session.snapshot()["turn_status"])

        root_turn_id = session.snapshot()["turn_id"]
        guided = session.append_project_guidance(
            project_id=project["project_id"],
            thread_id=project["root_thread_id"],
            text="preserve the new constraint",
        )
        self.assertTrue(guided["guidance_accepted"])
        self.assertEqual("steered", guided["workflow_delivery"]["mode"])
        self.assertEqual(
            "running", guided["project_team"]["projects"][0]["status"]
        )
        self.assertTrue(
            any(
                method == "turn/steer"
                and params["threadId"] == project["root_thread_id"]
                and params["expectedTurnId"] == root_turn_id
                for method, params in client.requests
            )
        )

        thread_starts = sum(
            method == "thread/start" for method, _params in client.requests
        )
        turn_starts = sum(
            method == "turn/start" for method, _params in client.requests
        )
        with self.assertRaises(BridgeError) as duplicate:
            session.start_turn("build a project", team_override="team")

        self.assertEqual(
            "PROJECT_THREAD_GUIDANCE_REQUIRED", duplicate.exception.code
        )
        self.assertEqual(
            project["project_id"], duplicate.exception.details["project_id"]
        )
        self.assertEqual(
            "supervisor", duplicate.exception.details["project_role"]
        )
        self.assertEqual(
            thread_starts,
            sum(method == "thread/start" for method, _params in client.requests),
        )
        self.assertEqual(
            turn_starts,
            sum(method == "turn/start" for method, _params in client.requests),
        )
        self.assertEqual(1, len(session.project_team_snapshot()["projects"]))
        session.interrupt_turn()

        class _ObservedRpcRejectionClient(_RootAckTimeoutClient):
            def request(
                self, method: str, params: Mapping[str, Any]
            ) -> dict[str, Any]:
                try:
                    return super().request(method, params)
                except BridgeError as exc:
                    if exc.code != "CODEX_REQUEST_TIMEOUT":
                        raise
                    raise CodexRPCError(
                        "turn/start",
                        {"message": "late rejection after turn/started"},
                    ) from exc

        rpc_client = _ObservedRpcRejectionClient()
        rpc_session = self._session(rpc_client)
        with self.assertRaises(CodexRPCError):
            rpc_session.start_turn("build a project", team_override="team")
        self.assertEqual(
            "running",
            rpc_session.project_team_snapshot()["projects"][0]["status"],
        )
        self.assertTrue(rpc_session.snapshot()["turn_active"])
        rpc_session.interrupt_turn()

    def test_new_entry_routing_is_frozen_and_never_reapplied_by_resume(self) -> None:
        events = EventBuffer()
        team_client = _ScriptedClient(block={"root", "planning"})
        team_session = BridgeSession(REPOSITORY_ROOT, team_client, events)
        team_session.update_project_team_mode(mode="single")
        started_team = team_session.start_thread(team_override="team")
        self.assertEqual("team", started_team["routing"])
        initial_team = next(
            params
            for method, params in team_client.requests
            if method == "thread/start" and "threadSource" not in params
        )
        self.assertEqual(
            {**PROJECT_HIA_DISABLE_CONFIG, "multi_agent_mode": "proactive"},
            initial_team["config"],
        )
        self.assertEqual("read-only", initial_team["sandbox"])
        team_session.start_turn("建一个木屋", team_override="team")
        self.assertFalse(
            any(method == "thread/resume" for method, _params in team_client.requests)
        )
        team_session.interrupt_turn()

        single_client = _ScriptedClient()
        single_session = BridgeSession(REPOSITORY_ROOT, single_client, EventBuffer())
        single_session.update_project_team_mode(mode="team")
        started_single = single_session.start_thread(team_override="single")
        self.assertEqual("single", started_single["routing"])
        initial_single = next(
            params
            for method, params in single_client.requests
            if method == "thread/start" and "threadSource" not in params
        )
        self.assertEqual(PROJECT_HIA_ENABLE_CONFIG, initial_single["config"])
        self.assertNotIn("multi_agent_mode", initial_single["config"])
        result = single_session.start_turn("只修改一个已知参数", team_override="single")
        self.assertEqual("single", result["routing"])
        self.assertFalse(
            any(method == "thread/resume" for method, _params in single_client.requests)
        )

    def test_stop_interrupts_root_and_background_roles_while_root_is_active(self) -> None:
        client = _ScriptedClient(block={"root", "planning"})
        session = self._session(client)
        result = session.start_turn("建一个木屋", team_override="team")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "planning"
                for method, params in client.requests
            ):
                break
            time.sleep(0.01)

        stopped = session.interrupt_turn()

        self.assertTrue(stopped["project_interrupt"]["interrupted"])
        interrupted_threads = {
            params["threadId"]
            for method, params in client.requests
            if method == "turn/interrupt"
        }
        self.assertIn(client.root_thread_id, interrupted_threads)
        self.assertIn("thread-planning", interrupted_threads)
        self.assertEqual(
            "interrupted",
            session.project_team_snapshot()["projects"][0]["status"],
        )
        self.assertEqual(
            ["active", "paused"],
            [
                params["status"]
                for method, params in client.requests
                if method == "thread/goal/set"
            ],
        )
        self.assertEqual(result["turn_id"], stopped["turn_id"])
        self.assertFalse(
            any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            )
        )

    def test_stop_finds_project_after_root_completed_and_interrupts_background(self) -> None:
        client = _ScriptedClient(block={"planning"})
        session = self._session(client)
        session.start_turn("建一个木屋", team_override="team")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                not session.snapshot()["turn_active"]
                and any(
                    method == "turn/start"
                    and client.roles[params["threadId"]] == "planning"
                    for method, params in client.requests
                )
            ):
                break
            time.sleep(0.01)
        self.assertFalse(session.snapshot()["turn_active"])

        stopped = session.interrupt_turn()

        self.assertTrue(stopped["project_interrupt"]["interrupted"])
        self.assertIsNone(stopped["turn_id"])
        self.assertTrue(
            any(
                method == "turn/interrupt"
                and params["threadId"] == "thread-planning"
                for method, params in client.requests
            )
        )
        self.assertEqual(
            "interrupted",
            session.project_team_snapshot()["projects"][0]["status"],
        )
        self.assertFalse(
            any(
                method == "turn/start"
                and client.roles[params["threadId"]] == "execution"
                for method, params in client.requests
            )
        )

    def test_selected_role_streams_long_blueprint_and_real_subagent_events_only(self) -> None:
        client = _ScriptedClient(block={"planning"})
        session = self._session(client)
        session.start_turn("建一个木屋", team_override="team")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            active = [
                key
                for key in client._active
                if key[0] == "thread-planning"
            ]
            if not session.snapshot()["turn_active"] and active:
                break
            time.sleep(0.01)
        planning_turn = active[0][1]
        session.resume_thread("thread-planning")
        cursor = self.events.poll(0, timeout=0)["latest"]
        blueprint = "木屋完整施工蓝图步骤" * 15_000
        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": planning_turn,
                    "item": {
                        "id": "planning-blueprint",
                        "type": "agentMessage",
                        "text": blueprint,
                    },
                },
            }
        )
        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "item/started",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": planning_turn,
                    "item": {
                        "id": "collab-real-1",
                        "type": "collabAgentToolCall",
                        "status": "inProgress",
                    },
                },
            }
        )
        visible = self.events.poll(cursor, timeout=0)["events"]
        self.assertTrue(
            any(
                event.get("params", {}).get("item", {}).get("text") == blueprint
                for event in visible
            )
        )
        self.assertTrue(
            any(
                event.get("params", {}).get("item", {}).get("type")
                == "collabAgentToolCall"
                for event in visible
            )
        )

        session.resume_thread(client.root_thread_id)
        cursor = self.events.poll(0, timeout=0)["latest"]
        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "item/started",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": planning_turn,
                    "item": {
                        "id": "subagent-hidden-1",
                        "type": "subAgentActivity",
                        "status": "inProgress",
                    },
                },
            }
        )
        self.assertEqual([], self.events.poll(cursor, timeout=0)["events"])
        session.interrupt_turn()

    def test_selected_project_role_advances_to_exact_coordinator_continuation_turn(self) -> None:
        client = _ScriptedClient(block={"planning", "supervisor"})
        session = self._session(client)
        first = session.start_turn("建一个木屋", team_override="team")
        original_turn = first["turn_id"]

        deadline = time.monotonic() + 2.0
        planning_turn = None
        while time.monotonic() < deadline:
            planning_turn = next(
                (
                    turn_id
                    for (thread_id, turn_id), role in client._active.items()
                    if thread_id == "thread-planning" and role == "planning"
                ),
                None,
            )
            if planning_turn is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(planning_turn)
        client._active.pop(("thread-planning", planning_turn), None)
        client._emit_completed("thread-planning", planning_turn, _plan(), "completed")

        authorization_turn = None
        while time.monotonic() < deadline:
            authorization_turn = next(
                (
                    turn_id
                    for (thread_id, turn_id), role in client._active.items()
                    if thread_id == client.root_thread_id and role == "supervisor"
                ),
                None,
            )
            if authorization_turn is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(authorization_turn)

        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": client.root_thread_id,
                    "turn": {"id": authorization_turn, "status": "inProgress"},
                },
            }
        )
        state = session.snapshot()
        self.assertTrue(state["turn_active"])
        self.assertEqual(authorization_turn, state["turn_id"])

        session._on_client_event(
            {
                "type": "codex_notification",
                "method": "turn/started",
                "params": {
                    "threadId": client.root_thread_id,
                    "turn": {"id": original_turn, "status": "inProgress"},
                },
            }
        )
        self.assertEqual(authorization_turn, session.snapshot()["turn_id"])
        session.interrupt_turn()


class ProjectTeamSettingsTests(unittest.TestCase):
    def _path(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        runtime_tmp = REPOSITORY_ROOT / ".runtime" / "tmp"
        runtime_tmp.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=runtime_tmp)
        return directory, Path(directory.name) / "project-team.json"

    def test_snapshot_is_only_new_single_team_contract(self) -> None:
        snapshot = ProjectTeamSettings().snapshot()
        self.assertEqual(PROJECT_TEAM_SCHEMA, snapshot["schema"])
        self.assertEqual(
            {"mode": "single", "writable": True},
            snapshot["settings"],
        )
        self.assertEqual([], snapshot["projects"])
        self.assertNotIn("mode", snapshot)
        self.assertNotIn("boundary", snapshot)

    def test_write_persists_only_new_settings_schema_and_mode(self) -> None:
        directory, path = self._path()
        self.addCleanup(directory.cleanup)
        saved = ProjectTeamSettings(path).set_mode("team")
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema": PROJECT_TEAM_SETTINGS_SCHEMA, "mode": "team"},
            persisted,
        )
        self.assertEqual("team", saved["settings"]["mode"])
        with self.assertRaises(BridgeError):
            ProjectTeamSettings(path).set_mode("auto")

    def test_legacy_values_are_read_migration_only(self) -> None:
        for legacy, expected in (("off", "single"), ("suggest", "single"), ("auto", "team")):
            with self.subTest(legacy=legacy):
                directory, path = self._path()
                try:
                    path.write_text(
                        json.dumps(
                            {
                                "schema": "hia-project-team-settings/1",
                                "mode": legacy,
                            }
                        ),
                        encoding="utf-8",
                    )
                    settings = ProjectTeamSettings(path)
                    self.assertEqual(expected, settings.mode())
                    snapshot = settings.snapshot()
                    self.assertEqual(expected, snapshot["settings"]["mode"])
                    self.assertNotIn(legacy, json.dumps(snapshot, ensure_ascii=False))
                finally:
                    directory.cleanup()

    def test_failed_replace_leaves_no_temporary_file(self) -> None:
        directory, path = self._path()
        self.addCleanup(directory.cleanup)
        settings = ProjectTeamSettings(path)
        with mock.patch(
            "hia_bridge.project_threads.os.replace",
            side_effect=PermissionError("locked"),
        ):
            with self.assertRaises(BridgeError) as raised:
                settings.set_mode("team")
        self.assertEqual("PROJECT_TEAM_SETTINGS_UNAVAILABLE", raised.exception.code)
        self.assertEqual([], list(path.parent.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
