from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[2]
PANEL_LIB_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
sys.path.insert(0, str(PANEL_LIB_ROOT))

from hia_panel.project_team import (  # noqa: E402
    PROJECT_TEAM_MODE_LABELS,
    PROJECT_TEAM_ROLE_ORDER,
    PROJECT_TEAM_ROLE_TITLES,
    normalize_project_team,
    top_level_history_records,
)
from tests.unit.test_panel_wiring import (  # noqa: E402
    _make_panel,
    _project_guidance_ack,
)


class _ProjectTeamPageShim:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, object]] = []
        self.status = ""
        self.action_status = ""
        self.busy = False
        self.selected_projects: list[str] = []
        self.selected_threads: list[str] = []
        self.submitted_guidance: list[str] = []

    def set_snapshot(self, snapshot: dict[str, object]) -> None:
        self.snapshots.append(snapshot)

    def set_status(self, text: str) -> None:
        self.status = text

    def set_busy(self, busy: bool) -> None:
        self.busy = bool(busy)

    def set_action_status(self, text: str) -> None:
        self.action_status = text

    def select_project(self, project_id: str) -> bool:
        self.selected_projects.append(project_id)
        return True

    def select_thread(self, thread_id: str) -> bool:
        self.selected_threads.append(thread_id)
        return True

    def guidance_submitted(
        self,
        project_id: str,
        thread_id: str,
        text: str,
    ) -> None:
        del project_id, text
        self.submitted_guidance.append(thread_id)


def _select_turn_mode(panel, mode: str) -> None:
    panel.turn_single_button.setChecked(mode == "single")
    panel.turn_team_button.setChecked(mode == "team")


class ProjectTeamPresentationTests(unittest.TestCase):
    @staticmethod
    def _bridge_snapshot(
        *, mode: str = "team", writable: bool = True
    ) -> dict[str, object]:
        return {
            "schema": "hia-project-team/1",
            "revision": "snapshot-7",
            "mode": mode,
            "settings": {"mode": mode, "writable": writable},
            "state_status": "ready",
            "projects": [
                {
                    "project_id": "project-1",
                    "title": "Procedural chicken",
                    "status": "in_progress",
                    "stage": "搭建主体结构",
                    "updated_at": 1_752_825_700,
                    "progress": {
                        "completed": 2,
                        "total": 5,
                        "label": "2 / 5 stages",
                    },
                    "actions": {
                        "open_thread": True,
                        "append_guidance": True,
                    },
                    "threads": [
                        {
                            "role": role,
                            "thread_id": f"thread-{role}",
                            "title": f"Chicken | {role}",
                            "status": (
                                "completed" if role == "planning" else "in_progress"
                            ),
                            "model": "gpt-5.6-sol",
                            "progress": {"percent": 40, "label": "40%"},
                        }
                        for role in PROJECT_TEAM_ROLE_ORDER
                    ],
                }
            ],
        }

    def test_normalizer_builds_bounded_project_and_role_thread_view(self) -> None:
        unavailable = normalize_project_team(None)
        self.assertFalse(unavailable["available"])
        self.assertEqual("single", unavailable["mode"])
        self.assertFalse(unavailable["settings_writable"])
        self.assertEqual([], unavailable["projects"])

        available = normalize_project_team(self._bridge_snapshot())
        self.assertTrue(available["available"])
        self.assertEqual("team", available["mode"])
        self.assertTrue(available["settings_writable"])
        self.assertEqual("snapshot-7", available["revision"])
        project = available["projects"][0]
        self.assertEqual("project-1", project["project_id"])
        self.assertEqual("搭建主体结构", project["stage"])
        self.assertEqual(list(PROJECT_TEAM_ROLE_ORDER), [
            thread["role_key"] for thread in project["threads"]
        ])
        self.assertEqual("进行中", project["status_label"])
        self.assertEqual(40, project["progress"]["percent"])
        self.assertEqual(
            {f"thread-{role}" for role in PROJECT_TEAM_ROLE_ORDER},
            set(available["worker_thread_ids"]),
        )
        self.assertTrue(project["threads"][0]["can_open"])
        self.assertTrue(project["threads"][0]["can_guide"])

        overridden = self._bridge_snapshot()
        overridden["projects"][0]["threads"][0]["actions"] = {
            "open_thread": False,
            "append_guidance": False,
        }
        overridden_thread = normalize_project_team(overridden)["projects"][0][
            "threads"
        ][0]
        self.assertFalse(overridden_thread["can_open"])
        self.assertFalse(overridden_thread["can_guide"])

    def test_user_visible_mode_labels_are_outcome_oriented(self) -> None:
        self.assertEqual(
            {
                "single": "单个 AI",
                "team": "项目团队",
            },
            PROJECT_TEAM_MODE_LABELS,
        )
        self.assertEqual("监督 AI", PROJECT_TEAM_ROLE_TITLES["supervisor"])
        self.assertEqual("方案 AI", PROJECT_TEAM_ROLE_TITLES["planning"])

    def test_explicit_writable_capability_is_respected(self) -> None:
        snapshot = normalize_project_team(
            self._bridge_snapshot(mode="single", writable=False)
        )
        self.assertEqual("single", snapshot["mode"])
        self.assertFalse(snapshot["settings_writable"])

        missing_settings = normalize_project_team(
            {
                "schema": "hia-project-team-settings/1",
                "mode": "auto",
                "state_status": "ready",
            }
        )
        self.assertEqual("team", missing_settings["mode"])
        self.assertFalse(missing_settings["settings_writable"])

        legacy_suggest = normalize_project_team(
            {
                "schema": "hia-project-team-settings/1",
                "mode": "suggest",
                "settings": {"mode": "suggest", "writable": True},
            }
        )
        self.assertEqual("single", legacy_suggest["mode"])

    def test_top_level_history_folds_all_project_members(self) -> None:
        snapshot = normalize_project_team(self._bridge_snapshot())
        raw_threads = [
            {
                "thread_id": f"thread-{role}",
                "name": f"worker {role}",
                "updated_at": 1_752_825_600,
            }
            for role in PROJECT_TEAM_ROLE_ORDER
        ] + [
            {
                "thread_id": "independent-1",
                "name": "Independent session",
                "updated_at": 1_752_825_650,
            }
        ]
        top_level = top_level_history_records(snapshot, raw_threads)
        self.assertEqual(["project", "thread"], [
            record["kind"] for record in top_level
        ])
        self.assertEqual("project-1", top_level[0]["project_id"])
        self.assertEqual("independent-1", top_level[1]["thread_id"])
        self.assertFalse(any(
            record.get("thread_id", "").startswith("thread-")
            for record in top_level
        ))

    def test_project_and_ordinary_history_limits_are_independent(self) -> None:
        raw_snapshot = {
            "schema": "hia-project-team/1",
            "settings": {"mode": "team", "writable": True},
            "projects": [
                {
                    "project_id": f"project-{index}",
                    "title": f"Project {index}",
                    "updated_at": index,
                    "threads": [],
                }
                for index in range(25)
            ],
        }
        snapshot = normalize_project_team(raw_snapshot)
        ordinary = [
            {
                "thread_id": f"ordinary-{index}",
                "name": f"Ordinary {index}",
                "updated_at": 1000 + index,
            }
            for index in range(10)
        ]

        records = top_level_history_records(snapshot, ordinary, limit=20)

        self.assertEqual(35, len(records))
        self.assertEqual(
            25,
            sum(record["kind"] == "project" for record in records),
        )
        self.assertEqual(
            10,
            sum(record["kind"] == "thread" for record in records),
        )

        two_project_snapshot = dict(snapshot)
        two_project_snapshot["projects"] = snapshot["projects"][:2]
        two_project_snapshot["worker_thread_ids"] = ()
        records = top_level_history_records(
            two_project_snapshot,
            ordinary,
            limit=5,
        )
        self.assertEqual(2, sum(item["kind"] == "project" for item in records))
        self.assertEqual(5, sum(item["kind"] == "thread" for item in records))

    def test_snapshot_keeps_projects_and_worker_folding_through_backend_cap(
        self,
    ) -> None:
        raw_projects = [
            {
                "project_id": f"project-{index}",
                "title": f"Project {index}",
                "updated_at": index,
                "threads": [
                    {
                        "role": "execution",
                        "thread_id": f"worker-{index}",
                    }
                ],
            }
            for index in range(256)
        ]
        snapshot = normalize_project_team(
            {
                "schema": "hia-project-team/1",
                "revision": 256,
                "settings": {"mode": "team", "writable": True},
                "projects": raw_projects,
            }
        )

        self.assertEqual(256, len(snapshot["projects"]))
        self.assertEqual("project-40", snapshot["projects"][40]["project_id"])
        self.assertEqual("project-255", snapshot["projects"][-1]["project_id"])
        self.assertEqual(256, len(snapshot["worker_thread_ids"]))
        history = top_level_history_records(
            snapshot,
            [
                {
                    "thread_id": f"worker-{index}",
                    "updated_at": index,
                }
                for index in range(256)
            ]
            + [
                {
                    "thread_id": "ordinary-independent",
                    "name": "Independent task",
                    "updated_at": 1000,
                }
            ],
        )
        self.assertEqual(
            ["ordinary-independent"],
            [
                record["thread_id"]
                for record in history
                if record["kind"] == "thread"
            ],
        )

    def test_snapshot_deterministically_bounds_malicious_project_overflow(
        self,
    ) -> None:
        snapshot = normalize_project_team(
            {
                "schema": "hia-project-team/1",
                "revision": 300,
                "settings": {"mode": "team", "writable": True},
                "projects": [
                    {
                        "project_id": f"project-{index}",
                        "updated_at": index,
                        "threads": [
                            {
                                "role": "execution",
                                "thread_id": f"worker-{index}",
                            }
                        ],
                    }
                    for index in range(300)
                ],
            }
        )

        self.assertEqual(256, len(snapshot["projects"]))
        self.assertEqual("project-0", snapshot["projects"][0]["project_id"])
        self.assertEqual("project-255", snapshot["projects"][-1]["project_id"])
        self.assertEqual(
            {f"worker-{index}" for index in range(256)},
            set(snapshot["worker_thread_ids"]),
        )
        history = top_level_history_records(
            snapshot,
            [
                {"thread_id": f"worker-{index}", "updated_at": index}
                for index in range(256)
            ]
            + [
                {
                    "thread_id": "ordinary-overflow-case",
                    "updated_at": 1000,
                }
            ],
        )
        self.assertEqual(
            ["ordinary-overflow-case"],
            [
                record["thread_id"]
                for record in history
                if record["kind"] == "thread"
            ],
        )

    def test_history_waits_for_snapshot_instead_of_flashing_worker_threads(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id=None)
        raw_threads = [
            {
                "thread_id": f"thread-{role}",
                "name": f"worker {role}",
                "updated_at": 1_752_825_600,
            }
            for role in PROJECT_TEAM_ROLE_ORDER
        ] + [
            {
                "thread_id": "independent-1",
                "name": "Independent session",
                "updated_at": 1_752_825_650,
            }
        ]
        panel._project_team_pending = "project-team-get-request"

        panel._apply_threads(raw_threads)

        self.assertEqual(len(raw_threads), len(panel._thread_history))
        self.assertEqual(1, panel.history_combo.count())
        self.assertIsNone(panel.history_combo.currentData())
        self.assertEqual(
            "正在同步项目与普通任务…",
            panel.history_combo.itemText(0),
        )

        panel._apply_project_team_snapshot(self._bridge_snapshot())

        visible = [
            panel.history_combo.itemData(index)
            for index in range(panel.history_combo.count())
            if isinstance(panel.history_combo.itemData(index), dict)
        ]
        self.assertEqual(["project", "thread"], [item["kind"] for item in visible])
        self.assertEqual("independent-1", visible[1]["thread_id"])

    def test_dedicated_view_is_nested_plain_text_project_ui(self) -> None:
        panel_source = (
            PANEL_LIB_ROOT / "hia_panel" / "panel.py"
        ).read_text(encoding="utf-8")
        view_source = (
            PANEL_LIB_ROOT / "hia_panel" / "project_team_view.py"
        ).read_text(encoding="utf-8")
        model_source = (
            PANEL_LIB_ROOT / "hia_panel" / "project_team.py"
        ).read_text(encoding="utf-8")
        project_team_contract = view_source + model_source

        self.assertIn('self.project_team_sidebar_button.setText("项目团队")', panel_source)
        self.assertIn("项目与角色 Threads", view_source)
        self.assertIn("self.project_tree.addTopLevelItem(project_item)", view_source)
        self.assertIn("project_item.addChild(thread_item)", view_source)
        self.assertIn('"阶段 / 状态"', view_source)
        self.assertIn("threadOpenRequested", view_source)
        self.assertIn("modelChooserRequested", view_source)
        self.assertNotIn("guidanceRequested", view_source)
        self.assertNotIn("guidance_edit", view_source)
        self.assertNotIn("发送给所选 Thread", view_source)
        self.assertIn("中央输入区追加文字或图片", view_source)
        self.assertIn('QtWidgets.QPushButton("选择指导模型")', view_source)
        self.assertIn("下一条指导生效", panel_source + view_source)
        self.assertNotIn("切换下一轮模型", panel_source + view_source)
        self.assertNotIn("下一轮消息", panel_source + view_source)
        self.assertNotIn("collabAgentToolCall", view_source)
        self.assertNotIn("subAgentActivity", view_source)
        self.assertNotIn("team_combo", view_source)
        self.assertNotIn("goal", view_source.casefold())
        self.assertNotIn("responsibility", view_source)
        self.assertNotIn("职责", view_source)
        self.assertIn("QtCore.Qt.TextFormat.PlainText", view_source)
        self.assertNotIn('QtWidgets.QLabel("新任务类型")', panel_source)
        self.assertIn('QtWidgets.QLabel("默认新建")', panel_source)
        self.assertIn(
            "self.turn_team_override_group = QtWidgets.QButtonGroup(self)",
            panel_source,
        )
        self.assertIn(
            "self.turn_single_button = QtWidgets.QToolButton()",
            panel_source,
        )
        self.assertIn(
            "self.turn_team_button = QtWidgets.QToolButton()",
            panel_source,
        )
        self.assertIn(
            "self.turn_team_override_group.addButton(button)",
            panel_source,
        )
        self.assertNotIn("self.mode_group", view_source)
        self.assertNotIn("self.mode_single_button", view_source)
        self.assertNotIn("self.mode_team_button", view_source)
        self.assertNotIn("新任务如何推进", view_source)
        self.assertNotIn("默认处理方式", view_source)
        self.assertIn("class ProjectHistoryTree", view_source)
        self.assertIn('self._group_item("项目", "projects")', view_source)
        self.assertIn('self._group_item("普通任务", "tasks")', view_source)
        self.assertIn(
            "self.itemDoubleClicked.connect(self._emit_thread_open)",
            view_source,
        )
        self.assertIn("_configure_text_tool_button(button)", panel_source)
        self.assertIn("ToolButtonTextOnly", view_source)
        self.assertIn("button.setMinimumSize", view_source)
        self.assertIn("QtCore.Qt.FocusPolicy.StrongFocus", view_source)
        self.assertIn(
            "QtWidgets.QSizePolicy.Policy.MinimumExpanding",
            view_source,
        )
        self.assertIn("ResponsiveModeButtonPair", panel_source)
        self.assertIn("ResponsiveModeButtonPair", view_source)
        self.assertIn(
            "QtWidgets.QSizePolicy.Policy.Expanding",
            panel_source,
        )
        self.assertGreaterEqual(panel_source.count("setExclusive(True)"), 1)
        self.assertNotIn("turn_team_override_combo", panel_source)
        self.assertNotIn("mode_combo", view_source)
        self.assertNotIn("沿用默认", panel_source + view_source)
        self.assertNotIn("先展示分工再开始", panel_source + view_source)
        for role in PROJECT_TEAM_ROLE_ORDER:
            self.assertIn(f'"{role}"', model_source)
        for forbidden in (
            "Desktop",
            "app-server",
            "串行",
            "关闭：",
            "建议：",
            "自动：",
            "沿用默认",
            "先展示分工再开始",
        ):
            self.assertNotIn(forbidden, view_source)

    def test_turn_routing_choice_does_not_require_a_selected_thread(self) -> None:
        panel = _make_panel(selected_thread_id=None)

        panel._refresh_controls()

        self.assertTrue(panel.turn_single_button.isEnabled())
        self.assertTrue(panel.turn_team_button.isEnabled())

        panel._turn_start_request_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())

        panel._turn_start_request_pending = False
        panel._turn_steer_request_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())

        panel._turn_steer_request_pending = False
        panel._interrupt_pending = True
        panel._refresh_controls()
        self.assertFalse(panel.turn_single_button.isEnabled())
        self.assertFalse(panel.turn_team_button.isEnabled())

    def test_missing_or_failed_snapshot_releases_history_sync_placeholder(
        self,
    ) -> None:
        ordinary = [
            {
                "thread_id": "ordinary-1",
                "name": "普通建模任务",
                "updated_at": 1_752_825_650,
            }
        ]
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._project_team_pending = "project-team-get"
        panel._apply_threads(ordinary)
        self.assertIn("正在同步", panel.history_combo.itemText(0))

        panel._on_action_completed("project_team:get", {})

        visible = [
            panel.history_combo.itemData(index)
            for index in range(panel.history_combo.count())
            if isinstance(panel.history_combo.itemData(index), dict)
        ]
        self.assertEqual(["ordinary-1"], [item["thread_id"] for item in visible])

        panel._project_team_pending = "project-team-get"
        panel._apply_threads(ordinary)
        panel._on_request_failed(
            "project_team:get",
            {
                "structured_error": {
                    "code": "PROJECT_SNAPSHOT_UNAVAILABLE",
                    "message": "snapshot unavailable",
                }
            },
        )
        visible = [
            panel.history_combo.itemData(index)
            for index in range(panel.history_combo.count())
            if isinstance(panel.history_combo.itemData(index), dict)
        ]
        self.assertEqual(["ordinary-1"], [item["thread_id"] for item in visible])

    def test_history_titles_never_fall_back_to_short_thread_ids(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        thread_id = "019fa304-240e-7210-953a-0faeab894eed"

        self.assertEqual("未命名任务", panel._history_title(thread_id))
        label = panel._history_label(
            {"kind": "thread", "thread_id": thread_id, "updated_at": None}
        )
        self.assertIn("未命名任务", label)
        self.assertNotIn("019f", label)
        self.assertNotIn("4eed", label)

        raw = self._bridge_snapshot()
        raw["projects"][0]["threads"][0].pop("title")
        normalized = normalize_project_team(raw)
        self.assertEqual(
            "监督 AI 任务",
            normalized["projects"][0]["threads"][0]["title"],
        )

    def test_panel_get_and_mode_save_use_only_project_team_endpoint(self) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page

        panel._refresh_project_team()
        self.assertEqual(1, panel._client.project_team_get_requests)
        self.assertTrue(page.busy)
        panel._on_action_completed(
            "project_team:get", {"project_team": self._bridge_snapshot()}
        )
        self.assertFalse(page.busy)
        self.assertTrue(page.snapshots[-1]["settings_writable"])

        panel._save_project_team_mode("single")
        self.assertEqual(["single"], panel._client.project_team_mode_requests)
        self.assertEqual([], panel._client.turn_requests)
        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual([], panel._client.thread_requests)

    def test_project_update_during_pending_response_triggers_followup_get(
        self,
    ) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        stale_response = self._bridge_snapshot()
        stale_response["revision"] = 1
        stale_response["projects"][0]["stage"] = "响应构造时的阶段"

        panel._refresh_project_team()
        panel._render_event(
            {
                "type": "project_team_updated",
                "project_id": "project-1",
            }
        )
        panel._render_event(
            {
                "type": "project_team_updated",
                "project_id": "project-1",
            }
        )
        self.assertEqual(1, panel._client.project_team_get_requests)
        self.assertTrue(panel._project_team_refresh_deferred)

        panel._on_action_completed(
            "project_team:get",
            {"project_team": stale_response},
        )

        self.assertEqual(2, panel._client.project_team_get_requests)
        self.assertFalse(panel._project_team_refresh_deferred)
        self.assertIsNotNone(panel._project_team_pending)
        fresh_response = copy.deepcopy(stale_response)
        fresh_response["revision"] = 2
        fresh_response["projects"][0]["stage"] = "事件后的新阶段"
        panel._on_action_completed(
            "project_team:get",
            {"project_team": fresh_response},
        )
        self.assertEqual(
            "事件后的新阶段",
            panel._project_team_snapshot["projects"][0]["stage"],
        )

    def test_project_update_during_failed_request_still_triggers_followup_get(
        self,
    ) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._refresh_project_team()
        panel._render_event(
            {
                "type": "project_team_updated",
                "project_id": "project-1",
            }
        )

        panel._on_request_failed(
            "project_team:get",
            {
                "structured_error": {
                    "code": "PROJECT_SNAPSHOT_UNAVAILABLE",
                    "message": "snapshot unavailable",
                }
            },
        )

        self.assertEqual(2, panel._client.project_team_get_requests)
        self.assertFalse(panel._project_team_refresh_deferred)
        self.assertIsNotNone(panel._project_team_pending)

    def test_panel_disables_save_when_bridge_reports_read_only(self) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(
            self._bridge_snapshot(writable=False)
        )

        self.assertFalse(page.snapshots[-1]["settings_writable"])
        panel._save_project_team_mode("team")
        self.assertEqual([], panel._client.project_team_mode_requests)

    def test_project_thread_actions_reuse_session_model_and_message_paths(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())

        panel._open_project_thread("thread-execution")
        self.assertEqual(
            [("thread-execution", None, "session_resume")],
            panel._client.resume_requests,
        )
        self.assertEqual(["thread-execution"], page.selected_threads)

        panel._session_action_pending = False
        panel._send_project_thread_guidance(
            "project-1",
            "thread-planning",
            "Keep the silhouette readable.",
        )
        request = panel._client.project_team_guidance_requests[-1]
        self.assertEqual("project-1", request[0])
        self.assertEqual("thread-planning", request[1])
        self.assertEqual("Keep the silhouette readable.", request[2])
        self.assertTrue(request[-1].startswith("project_team:guide:"))
        self.assertEqual([], page.submitted_guidance)
        self.assertTrue(page.busy)

        panel._on_action_completed(
            request[-1],
            {"project_team": self._bridge_snapshot()},
        )

        self.assertEqual([], page.submitted_guidance)
        self.assertFalse(page.busy)
        self.assertIn("权威校验", page.action_status)

        panel._send_project_thread_guidance(
            "project-1",
            "thread-planning",
            "Keep the silhouette readable.",
        )
        request = panel._client.project_team_guidance_requests[-1]
        panel._on_action_completed(
            request[-1],
            {
                **_project_guidance_ack(
                    "project-1",
                    "thread-planning",
                    "planning",
                ),
                "project_team": self._bridge_snapshot(),
            },
        )

        self.assertEqual(["thread-planning"], page.submitted_guidance)
        self.assertFalse(page.busy)

    def test_project_model_switch_tracks_the_exact_selected_thread(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())

        panel._choose_project_thread_model("thread-technical_review")

        self.assertEqual(
            [("thread-technical_review", None, "session_resume")],
            panel._client.resume_requests,
        )
        self.assertEqual(
            "thread-technical_review",
            panel._project_team_model_thread_id,
        )

        panel._on_action_completed(
            "session_resume",
            {"thread_id": "thread-technical_review"},
        )

        self.assertIsNone(panel._project_team_model_thread_id)
        self.assertEqual(1, panel.model_combo.set_focus_calls)
        self.assertEqual(
            ["thread-technical_review"],
            page.selected_threads[-1:],
        )

    def test_long_blueprint_stays_out_of_tree_but_renders_complete_in_chat(self) -> None:
        long_blueprint = "阶段卡｜结构、验收与证据\n" + ("完整蓝图正文。" * 20_000)
        raw_snapshot = self._bridge_snapshot()
        raw_project = raw_snapshot["projects"][0]
        raw_project["blueprint"] = long_blueprint
        raw_project["threads"][1]["blueprint"] = long_blueprint

        snapshot = normalize_project_team(raw_snapshot)

        self.assertNotIn("blueprint", snapshot["projects"][0])
        self.assertNotIn("blueprint", snapshot["projects"][0]["threads"][1])

        panel = _make_panel(selected_thread_id="thread-planning")
        rendered = panel._render_thread_read(
            {
                "result": {
                    "thread": {
                        "id": "thread-planning",
                        "turns": [
                            {
                                "items": [
                                    {
                                        "type": "agentMessage",
                                        "text": long_blueprint,
                                    }
                                ]
                            }
                        ],
                    }
                }
            }
        )

        self.assertTrue(rendered)
        self.assertEqual(long_blueprint, panel.conversation.entries[-1]["text"])

    def test_open_role_streams_only_that_role_into_central_chat(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        panel._open_project_thread("thread-planning")
        panel._on_action_completed(
            "session_resume",
            {
                "thread_id": "thread-planning",
                "turn_active": True,
                "turn_id": "turn-planning-live",
                "turn_status": "inProgress",
                "read": {
                    "thread": {"id": "thread-planning", "turns": []}
                },
            },
        )
        self.assertEqual("thread-planning", panel._selected_thread_id)
        self.assertTrue(panel._turn_state.busy)
        self.assertEqual("thread-planning", panel._stream_thread_id)
        self.assertEqual("turn-planning-live", panel._stream_turn_id)

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-execution",
                    "turnId": "turn-execution-background",
                    "itemId": "background-message",
                    "delta": "后台执行角色不得串入当前聊天",
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": "turn-planning-live",
                    "itemId": "selected-message",
                    "delta": "方案角色的实时新消息",
                },
            }
        )

        transcript = panel.conversation.toPlainText()
        self.assertIn("方案角色的实时新消息", transcript)
        self.assertNotIn("后台执行角色不得串入当前聊天", transcript)

    def test_running_role_can_switch_model_and_send_text_with_image(self) -> None:
        panel = _make_panel(selected_thread_id="thread-supervisor")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        self.assertTrue(panel._turn_state.begin_start("thread-supervisor"))
        start_token = panel._turn_state.capture_token()
        self.assertTrue(panel._turn_state.acknowledge_start(
            start_token,
            "thread-supervisor",
            "turn-supervisor-running",
        ))
        panel._stream_thread_id = "thread-supervisor"
        panel._stream_turn_id = "turn-supervisor-running"
        panel.input_edit.setPlainText("切换失败也必须保留")
        panel.attachment_strip.add_path("E:/references/source.png")

        panel._choose_project_thread_model("thread-execution")

        self.assertEqual(
            [("thread-execution", None, "session_resume")],
            panel._client.resume_requests,
        )
        self.assertEqual(
            "thread-supervisor",
            panel._turn_state.thread_id,
        )
        self.assertTrue(panel._turn_state.busy)
        self.assertEqual(
            {
                "source_thread_id": "thread-supervisor",
                "target_thread_id": "thread-execution",
            },
            panel._pending_running_project_switch,
        )

        panel._on_request_failed(
            "session_resume",
            {
                "structured_error": {
                    "code": "NETWORK_TIMEOUT",
                    "message": "timed out",
                }
            },
        )
        self.assertEqual("thread-supervisor", panel._selected_thread_id)
        self.assertEqual("thread-supervisor", panel._turn_state.thread_id)
        self.assertTrue(panel._turn_state.busy)
        self.assertEqual("切换失败也必须保留", panel.input_edit.toPlainText())
        self.assertEqual(
            ["E:/references/source.png"],
            panel.attachment_strip.paths(),
        )

        panel._choose_project_thread_model("thread-execution")
        panel._on_action_completed(
            "session_resume",
            {
                "thread_id": "thread-execution",
                "turn_active": True,
                "turn_id": "turn-execution-running",
                "turn_status": "inProgress",
                "read": {"thread": {"id": "thread-execution", "turns": []}},
            },
        )

        self.assertEqual("thread-execution", panel._selected_thread_id)
        self.assertEqual("thread-execution", panel._turn_state.thread_id)
        self.assertEqual("turn-execution-running", panel._turn_state.turn_id)
        self.assertTrue(panel._turn_state.busy)
        self.assertIsNone(panel._pending_running_project_switch)
        self.assertEqual([], panel.attachment_strip.paths())
        self.assertIsNone(panel._project_team_model_thread_id)
        self.assertEqual(1, panel.model_combo.set_focus_calls)

        panel.input_edit.setPlainText("运行中追加图文")
        panel.attachment_strip.add_path("E:/references/target.png")
        panel._send()
        request = panel._client.project_team_guidance_requests[-1]
        self.assertEqual(
            ("project-1", "thread-execution", "运行中追加图文"),
            request[:3],
        )
        self.assertEqual(["E:/references/target.png"], request[-2])
        self.assertEqual([], panel._client.turn_requests)
        self.assertEqual([], panel._client.steer_requests)

    def test_role_ultra_activity_reuses_internal_agent_surface(self) -> None:
        panel = _make_panel(selected_thread_id="thread-planning")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        conversation_before = list(panel.conversation.entries)
        project_snapshots_before = len(page.snapshots)

        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": "turn-planning",
                    "item": {
                        "id": "collab-planning",
                        "type": "collabAgentToolCall",
                        "senderThreadId": "thread-planning",
                        "receiverThreadIds": ["ultra-child"],
                        "agentsStates": {
                            "ultra-child": {
                                "status": "running",
                                "role": "research",
                            }
                        },
                        "prompt": "核对蓝图证据",
                        "status": "completed",
                    },
                },
            }
        )
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "item/completed",
                "params": {
                    "threadId": "thread-planning",
                    "turnId": "turn-planning",
                    "item": {
                        "id": "activity-planning",
                        "type": "subAgentActivity",
                        "agentThreadId": "ultra-child",
                        "agentPath": "planning/research",
                        "kind": "started",
                        "summary": "证据核对中",
                    },
                },
            }
        )

        self.assertIn("ultra-child", panel._team_records)
        self.assertEqual(
            "thread-planning",
            panel._team_records["ultra-child"]["root_thread_id"],
        )
        self.assertEqual(conversation_before, panel.conversation.entries)
        self.assertEqual(project_snapshots_before, len(page.snapshots))

    def test_success_event_moves_selected_ordinary_thread_and_next_turn_token(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id="thread-1")
        panel.conversation.add_user_message("保留的用户消息", ())
        conversation_before = list(panel.conversation.entries)
        self.assertTrue(panel._turn_state.begin_start("thread-1"))
        self.assertTrue(
            panel._turn_state.confirm_start_not_created(
                "thread-1",
                no_active_turn=True,
            )
        )
        old_idle_token = panel._turn_state.capture_token()
        panel._stream_thread_id = "thread-1"
        panel._goal_continuation_boundary = ("thread-1", "turn-before-fork")
        panel._goal_continue_after_open_thread_id = "thread-1"
        panel._goal_stage_snapshot["thread_id"] = "thread-1"
        panel._current_goal = {"threadId": "thread-1", "status": "active"}
        panel._goal_auto_turn_token = old_idle_token
        panel._diagnostic_snapshot = {"thread_id": "thread-1"}
        panel._diagnostic_turn_key = "thread-1:turn-before-fork"
        panel._diagnostic_draft_key = "thread-1:draft:before-fork"

        event = {
            "type": "thread_transferred",
            "old_thread_id": "thread-1",
            "new_thread_id": "thread-forked",
        }
        panel._render_event(event)
        panel._render_event(
            {
                "type": "codex_notification",
                "method": "thread/deleted",
                "params": {"threadId": "thread-1"},
            }
        )
        panel._render_event(event)

        self.assertEqual("thread-forked", panel._selected_thread_id)
        self.assertEqual("thread-forked", panel.thread_id_edit.text())
        self.assertFalse(panel._turn_state.busy)
        self.assertEqual("thread-forked", panel._turn_state.thread_id)
        self.assertFalse(panel._turn_state.token_is_current(old_idle_token))
        self.assertEqual("thread-forked", panel._stream_thread_id)
        self.assertEqual(
            ("thread-forked", "turn-before-fork"),
            panel._goal_continuation_boundary,
        )
        self.assertEqual(
            "thread-forked",
            panel._goal_continue_after_open_thread_id,
        )
        self.assertEqual(
            "thread-forked",
            panel._goal_stage_snapshot["thread_id"],
        )
        self.assertEqual("thread-forked", panel._current_goal["threadId"])
        self.assertIsNone(panel._goal_auto_turn_token)
        self.assertEqual(
            "thread-forked",
            panel._diagnostic_snapshot["thread_id"],
        )
        self.assertEqual(
            "thread-forked:turn-before-fork",
            panel._diagnostic_turn_key,
        )
        self.assertEqual(
            "thread-forked:draft:before-fork",
            panel._diagnostic_draft_key,
        )
        self.assertNotIn(
            "thread-1",
            {
                record["thread_id"]
                for record in panel._thread_history
                if isinstance(record.get("thread_id"), str)
            },
        )
        self.assertEqual(
            "thread-forked",
            panel._thread_history[0]["thread_id"],
        )
        self.assertEqual(
            "thread-forked",
            panel.history_combo.currentData()["thread_id"],
        )
        self.assertEqual(conversation_before, panel.conversation.entries[:1])
        transcript = panel.conversation.toPlainText()
        self.assertIn("保留的用户消息", transcript)
        self.assertEqual(
            1,
            transcript.count("上下文已迁移到新任务，旧任务已删除。"),
        )

        panel.input_edit.setPlainText("迁移后的下一条指令")
        panel._send()
        next_context = panel._client.turn_requests[-1][-1]
        next_token = panel._turn_start_tokens[next_context]
        self.assertEqual("thread-forked", next_token.thread_id)
        self.assertGreater(next_token.generation, old_idle_token.generation)
        panel._on_action_completed(
            next_context,
            {
                "ok": True,
                "thread_id": "thread-forked",
                "turn_id": "turn-after-fork",
                "turn_active": True,
                "turn_status": "inProgress",
                "routing": "single",
            },
        )
        self.assertEqual("thread-forked", panel._turn_state.thread_id)
        self.assertEqual("turn-after-fork", panel._turn_state.turn_id)

    def test_success_event_and_snapshot_only_move_selected_project_role(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id="thread-planning")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        raw_snapshot = self._bridge_snapshot()
        panel._apply_project_team_snapshot(raw_snapshot)
        self.assertTrue(panel._turn_state.begin_start("thread-planning"))
        self.assertTrue(
            panel._turn_state.confirm_start_not_created(
                "thread-planning",
                no_active_turn=True,
            )
        )
        project_idle_token = panel._turn_state.capture_token()
        panel._selected_project_id = "project-before-transfer"
        panel._project_team_model_thread_id = "thread-planning"

        panel._render_event(
            {
                "type": "thread_transferred",
                "old_thread_id": "thread-planning",
                "new_thread_id": "thread-planning-forked",
                "project_id": "project-1",
                "role": "planning",
            }
        )

        project = panel._project_team_snapshot["projects"][0]
        planning = [
            thread
            for thread in project["threads"]
            if thread["role_key"] == "planning"
        ]
        self.assertEqual(1, len(planning))
        self.assertEqual("thread-planning-forked", planning[0]["thread_id"])
        self.assertNotIn(
            "thread-planning",
            panel._project_team_snapshot["worker_thread_ids"],
        )
        self.assertEqual(
            "thread-planning-forked",
            panel._selected_thread_id,
        )
        self.assertEqual("thread-planning-forked", panel._turn_state.thread_id)
        self.assertFalse(panel._turn_state.token_is_current(project_idle_token))
        self.assertEqual("project-1", panel._selected_project_id)
        self.assertEqual(
            "thread-planning-forked",
            panel._project_team_model_thread_id,
        )
        self.assertEqual(
            ["thread-planning-forked"],
            page.selected_threads[-1:],
        )
        top_level = top_level_history_records(
            panel._project_team_snapshot,
            panel._thread_history,
        )
        self.assertEqual(
            1,
            sum(record.get("kind") == "project" for record in top_level),
        )

        fallback_panel = _make_panel(selected_thread_id="thread-planning")
        fallback_page = _ProjectTeamPageShim()
        fallback_panel.project_team_page = fallback_page
        fallback_panel._apply_project_team_snapshot(raw_snapshot)
        transferred_snapshot = copy.deepcopy(raw_snapshot)
        transferred_snapshot["revision"] = "snapshot-8"
        transferred_snapshot["projects"][0]["threads"][1][
            "thread_id"
        ] = "thread-planning-forked"
        fallback_panel._apply_project_team_snapshot(transferred_snapshot)
        self.assertEqual(
            "thread-planning-forked",
            fallback_panel._selected_thread_id,
        )
        self.assertFalse(fallback_panel._turn_state.busy)
        self.assertEqual(
            "thread-planning-forked",
            fallback_panel._turn_state.thread_id,
        )
        self.assertEqual(
            1,
            fallback_panel.conversation.toPlainText().count(
                "上下文已迁移到新任务，清理状态待同步。"
            ),
        )
        self.assertNotIn(
            "上下文已迁移到新任务，旧任务已删除。",
            fallback_panel.conversation.toPlainText(),
        )
        fallback_panel._render_event(
            {
                "type": "thread_transferred",
                "old_thread_id": "thread-planning",
                "new_thread_id": "thread-planning-forked",
                "project_id": "project-1",
                "role": "planning",
            }
        )
        self.assertEqual(
            1,
            fallback_panel.conversation.toPlainText().count(
                "上下文已迁移到新任务，旧任务已删除。"
            ),
        )

    def test_older_project_snapshot_cannot_overwrite_newer_guidance_state(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id="thread-planning")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        older = self._bridge_snapshot()
        older["revision"] = 7
        older["projects"][0]["stage"] = "旧阶段"
        newer = copy.deepcopy(older)
        newer["revision"] = 8
        newer["projects"][0]["stage"] = "指导后的新阶段"

        panel._apply_project_team_snapshot(newer)
        redraws = len(page.snapshots)
        panel._apply_project_team_snapshot(older)

        self.assertEqual(8, panel._project_team_snapshot["revision"])
        self.assertEqual(
            "指导后的新阶段",
            panel._project_team_snapshot["projects"][0]["stage"],
        )
        self.assertEqual(redraws, len(page.snapshots))

    def test_first_authoritative_revision_zero_is_not_blocked_by_placeholder(
        self,
    ) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._project_team_snapshot = normalize_project_team({"revision": 0})
        panel._project_team_revision_authoritative = False
        first_snapshot = self._bridge_snapshot()
        first_snapshot["revision"] = 0

        panel._apply_project_team_snapshot(first_snapshot)

        self.assertTrue(panel._project_team_snapshot["available"])
        self.assertEqual(0, panel._project_team_snapshot["revision"])
        self.assertTrue(panel._project_team_revision_authoritative)
        self.assertEqual(1, len(page.snapshots))

    def test_reconnected_bridge_generation_can_restart_revision_sequence(
        self,
    ) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        old_generation = self._bridge_snapshot()
        old_generation["revision"] = 90
        old_generation["projects"][0]["stage"] = "旧 Bridge 阶段"
        panel._apply_project_team_snapshot(old_generation)
        panel._connected = False
        panel._reconnecting = True

        panel._on_health(
            {
                "session": {
                    "connected": True,
                    "authentication": "authenticated",
                    "thread_id": "thread-1",
                    "turn_active": False,
                }
            }
        )

        self.assertFalse(panel._project_team_revision_authoritative)
        self.assertEqual(1, panel._client.project_team_get_requests)
        new_generation = self._bridge_snapshot()
        new_generation["revision"] = 1
        new_generation["projects"][0]["stage"] = "新 Bridge 阶段"
        panel._on_action_completed(
            "project_team:get",
            {"project_team": new_generation},
        )
        self.assertEqual(1, panel._project_team_snapshot["revision"])
        self.assertEqual(
            "新 Bridge 阶段",
            panel._project_team_snapshot["projects"][0]["stage"],
        )
        self.assertTrue(panel._project_team_revision_authoritative)

    def test_old_snapshot_after_transfer_never_rebinds_deleted_thread(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id="thread-planning")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        old_snapshot = self._bridge_snapshot()
        old_snapshot["revision"] = 7
        panel._apply_project_team_snapshot(old_snapshot)
        new_snapshot = copy.deepcopy(old_snapshot)
        new_snapshot["revision"] = 8
        new_snapshot["projects"][0]["threads"][1][
            "thread_id"
        ] = "thread-planning-forked"

        panel._apply_project_team_snapshot(new_snapshot)
        self.assertEqual("thread-planning-forked", panel._selected_thread_id)
        panel._apply_project_team_snapshot(old_snapshot)

        self.assertEqual("thread-planning-forked", panel._selected_thread_id)
        self.assertIn(
            "thread-planning-forked",
            panel._project_team_snapshot["worker_thread_ids"],
        )
        self.assertNotIn(
            "thread-planning",
            panel._project_team_snapshot["worker_thread_ids"],
        )

    def test_equal_revision_conflict_is_ignored_without_transfer(self) -> None:
        panel = _make_panel(selected_thread_id="thread-planning")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        current = self._bridge_snapshot()
        current["revision"] = 9
        panel._apply_project_team_snapshot(current)
        conflicting = copy.deepcopy(current)
        conflicting["projects"][0]["threads"][1][
            "thread_id"
        ] = "thread-planning-conflict"
        redraws = len(page.snapshots)

        panel._apply_project_team_snapshot(conflicting)

        self.assertEqual("thread-planning", panel._selected_thread_id)
        self.assertIn(
            "thread-planning",
            panel._project_team_snapshot["worker_thread_ids"],
        )
        self.assertNotIn(
            "thread-planning-conflict",
            panel._project_team_snapshot["worker_thread_ids"],
        )
        self.assertEqual(redraws, len(page.snapshots))

    def test_failure_event_preserves_every_old_identity_and_shows_error(
        self,
    ) -> None:
        panel = _make_panel(selected_thread_id="thread-1")
        self.assertTrue(panel._turn_state.begin_start("thread-1"))
        self.assertTrue(
            panel._turn_state.confirm_start_not_created(
                "thread-1",
                no_active_turn=True,
            )
        )
        old_idle_token = panel._turn_state.capture_token()
        panel._stream_thread_id = "thread-1"
        panel._selected_project_id = "project-stays-selected"
        panel._goal_stage_snapshot["thread_id"] = "thread-1"
        panel._diagnostic_snapshot = {"thread_id": "thread-1"}

        panel._render_event(
            {
                "type": "thread_transferred",
                "old_thread_id": "thread-1",
                "new_thread_id": None,
                "error": "fork <verification> failed",
            }
        )

        self.assertEqual("thread-1", panel._selected_thread_id)
        self.assertEqual("thread-1", panel._thread_history[0]["thread_id"])
        self.assertTrue(panel._turn_state.token_is_current(old_idle_token))
        self.assertEqual("thread-1", panel._turn_state.thread_id)
        self.assertEqual("thread-1", panel._stream_thread_id)
        self.assertEqual("project-stays-selected", panel._selected_project_id)
        self.assertEqual("thread-1", panel._goal_stage_snapshot["thread_id"])
        self.assertEqual("thread-1", panel._diagnostic_snapshot["thread_id"])
        transcript = panel.conversation.toPlainText()
        self.assertIn("上下文迁移失败，已保留原任务。", transcript)
        self.assertIn("fork ＜verification＞ failed", transcript)

    def test_background_role_transfer_never_pollutes_current_chat(self) -> None:
        panel = _make_panel(selected_thread_id="ordinary-current")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        panel.conversation.add_user_message("当前普通任务内容", ())
        transcript_before = panel.conversation.toPlainText()

        panel._render_event({
            "type": "thread_transferred",
            "old_thread_id": "thread-planning",
            "new_thread_id": "thread-planning-new",
            "project_id": "project-1",
            "role": "planning",
        })
        panel._render_event({
            "type": "thread_transferred",
            "old_thread_id": "thread-execution",
            "new_thread_id": None,
            "project_id": "project-1",
            "role": "execution",
            "error": "background fork failed",
        })

        self.assertEqual(transcript_before, panel.conversation.toPlainText())
        self.assertNotIn(
            "上下文已迁移",
            panel.conversation.toPlainText(),
        )
        self.assertNotIn(
            "上下文迁移失败",
            panel.conversation.toPlainText(),
        )
        self.assertEqual("ordinary-current", panel._selected_thread_id)

    def test_project_container_selection_never_resumes_a_worker_implicitly(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        project_index = next(
            index
            for index in range(panel.history_combo.count())
            if isinstance(panel.history_combo.itemData(index), dict)
            and panel.history_combo.itemData(index).get("kind") == "project"
        )
        panel.history_combo.setCurrentIndex(project_index)
        panel._on_history_index_changed(project_index)
        panel._resume_thread()

        self.assertEqual([], panel._client.resume_requests)
        self.assertEqual(["project-1"], page.selected_projects[-1:])

    def test_clicked_role_opens_exact_thread_and_keeps_child_selected(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        child_record = {
            "kind": "thread",
            "project_id": "project-1",
            "thread_id": "thread-execution",
            "name": "执行 AI 任务",
            "managed_project_thread": True,
        }
        panel.history_combo.clear()
        panel.history_combo.addItem("执行 AI", child_record)
        panel.history_combo.setCurrentIndex(0)
        panel._on_history_index_changed(0)

        panel._open_history_thread("thread-execution")

        self.assertEqual(
            [("thread-execution", None, "session_resume")],
            panel._client.resume_requests,
        )
        self.assertEqual(child_record, panel.history_combo.currentData())
        self.assertEqual(["thread-execution"], page.selected_threads[-1:])

    def test_group_header_never_resumes_a_thread(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        panel.history_combo.clear()
        panel.history_combo.addItem(
            "项目",
            {"kind": "group", "group": "projects"},
        )
        panel.history_combo.setCurrentIndex(0)
        panel._on_history_index_changed(0)

        panel._resume_thread()

        self.assertEqual([], panel._client.resume_requests)
        self.assertFalse(panel.resume_thread_button.isEnabled())

    def test_headless_combo_fallback_folds_current_worker_to_project(self) -> None:
        panel = _make_panel(selected_thread_id="thread-execution")
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())

        selected = panel.history_combo.currentData()
        self.assertEqual("project", selected["kind"])
        self.assertEqual("project-1", selected["project_id"])

    def test_existing_task_route_cannot_be_changed_by_new_task_buttons(self) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(
            self._bridge_snapshot(mode="single")
        )
        _select_turn_mode(panel, "team")
        panel.input_edit.setPlainText("Build a procedural chicken.")

        panel._send()
        context = panel._client.turn_requests[-1][-1]
        self.assertEqual("single", panel._client.turn_team_overrides[-1])
        self.assertTrue(panel.turn_single_button.isChecked())
        self.assertFalse(panel.turn_team_button.isChecked())

        panel._on_action_completed(
            context,
            {
                "thread_id": "thread-1",
                "turn_id": "turn-team",
                "turn_active": True,
                "routing": "single",
            },
        )
        self.assertTrue(panel.turn_single_button.isChecked())
        self.assertFalse(panel.turn_team_button.isChecked())
        self.assertEqual(1, len(panel._client.turn_team_overrides))
        self.assertEqual("single", panel._selected_team_override())

    def test_saved_default_updates_initial_choice_without_overwriting_draft(self) -> None:
        panel = _make_panel(selected_thread_id=None)
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        team_snapshot = self._bridge_snapshot(mode="team")
        panel._apply_project_team_snapshot(team_snapshot)
        self.assertEqual("team", panel._selected_team_override())

        _select_turn_mode(panel, "single")
        panel._apply_project_team_snapshot(team_snapshot)
        self.assertEqual("single", panel._selected_team_override())

    def test_unrelated_thread_notifications_do_not_change_project_snapshot(self) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        original = copy.deepcopy(panel._project_team_snapshot)

        for method in (
            "thread/settings/updated",
            "thread/status/changed",
            "thread/archived",
            "thread/unarchived",
            "thread/deleted",
        ):
            panel._render_event(
                {
                    "type": "codex_notification",
                    "method": method,
                    "params": {"threadId": "desktop-looking-id"},
                }
            )
            self.assertEqual(original, panel._project_team_snapshot)

    def test_only_dedicated_action_payload_applies_project_team_mode(self) -> None:
        panel = _make_panel()
        page = _ProjectTeamPageShim()
        panel.project_team_page = page
        panel._apply_project_team_snapshot(self._bridge_snapshot())
        original = copy.deepcopy(panel._project_team_snapshot)
        foreign = self._bridge_snapshot(mode="single")

        panel._on_health({"session": {}, "project_team": foreign})
        self.assertEqual(original, panel._project_team_snapshot)
        panel._on_session({"session": {}, "project_team": foreign})
        self.assertEqual(original, panel._project_team_snapshot)
        panel._project_team_pending = "alias-response"
        panel._on_action_completed("project_team:get", {"state": foreign})
        self.assertEqual(original, panel._project_team_snapshot)


if __name__ == "__main__":
    unittest.main()
