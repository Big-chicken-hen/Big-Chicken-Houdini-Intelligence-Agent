"""Pure view models for the compact project-team Panel.

The module intentionally has no Qt or Bridge dependency.  It turns the public
Bridge snapshots into stable identities that a tree view can render without
guessing project membership from names, cwd, or model strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping


ROLE_ORDER = (
    "supervisor",
    "planning",
    "execution",
    "visual_review",
    "technical_review",
)

ROLE_TITLES = {
    "supervisor": "监督 AI",
    "planning": "方案 AI",
    "execution": "执行 AI",
    "visual_review": "视觉审查 AI",
    "technical_review": "技术审查 AI",
}

TERMINAL_PROJECT_STATUSES = frozenset(
    {"completed", "failed", "not_applicable"}
)


def _text(value: Any, *, limit: int = 512) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(
        " " if ord(character) < 32 and character not in "\n\t" else character
        for character in value
    ).strip()[:limit]


def _optional_text(value: Any, *, limit: int = 512) -> str | None:
    rendered = _text(value, limit=limit)
    return rendered or None


def _boolean_action(actions: Any, name: str) -> bool:
    return isinstance(actions, Mapping) and actions.get(name) is True


@dataclass(frozen=True)
class RoleRuntimeDraft:
    """The next-Turn settings for one real project-role Thread."""

    model: str | None = None
    effort: str | None = None
    service_tier: str | None = None


@dataclass(frozen=True)
class RoleViewModel:
    project_id: str
    role: str
    title: str
    thread_id: str
    status: str
    runtime: RoleRuntimeDraft
    can_open: bool
    can_guide: bool
    can_set_runtime: bool

    @property
    def stable_key(self) -> str:
        # Survives a verified native Thread transfer for the same project role.
        return f"role:{self.project_id}:{self.role}"


@dataclass(frozen=True)
class AttentionViewModel:
    visible: bool = False
    reason: str | None = None
    stage: str | None = None
    consumed_turns: int = 0
    last_error: str | None = None
    latest_evidence_ids: tuple[str, ...] = ()
    latest_repair_card: str | None = None


@dataclass(frozen=True)
class RequirementViewModel:
    requirement_id: str
    kind: str
    status: str


@dataclass(frozen=True)
class ProjectViewModel:
    project_id: str
    title: str
    status: str
    stage: str | None
    roles: tuple[RoleViewModel, ...]
    requirements: tuple[RequirementViewModel, ...]
    attention: AttentionViewModel
    can_continue: bool
    can_guide: bool
    can_stop: bool

    @property
    def stable_key(self) -> str:
        return f"project:{self.project_id}"

    @property
    def opens_chat(self) -> bool:
        """A project is a container, never a synthetic conversation."""

        return False


@dataclass(frozen=True)
class OrdinaryThreadViewModel:
    thread_id: str
    title: str
    preview: str
    updated_at: int

    @property
    def stable_key(self) -> str:
        return f"thread:{self.thread_id}"

    @property
    def opens_chat(self) -> bool:
        return True


@dataclass(frozen=True)
class WorkspaceTreeViewModel:
    projects: tuple[ProjectViewModel, ...] = ()
    ordinary_threads: tuple[OrdinaryThreadViewModel, ...] = ()
    default_route: str = "single"
    state_status: str = "ready"

    @property
    def role_thread_ids(self) -> frozenset[str]:
        return frozenset(
            role.thread_id
            for project in self.projects
            for role in project.roles
        )


@dataclass
class ProjectPanelState:
    """Selection and draft state that remains stable across snapshots."""

    tree: WorkspaceTreeViewModel = field(default_factory=WorkspaceTreeViewModel)
    selected_key: str | None = None
    collapsed: bool = False
    projects_group_expanded: bool = True
    ordinary_group_expanded: bool = True
    project_expanded: dict[str, bool] = field(default_factory=dict)
    runtime_drafts: dict[str, RoleRuntimeDraft] = field(default_factory=dict)

    def apply_snapshot(
        self,
        project_team: Any,
        ordinary_threads: Any = None,
    ) -> WorkspaceTreeViewModel:
        previous_key = self.selected_key
        self.tree = normalize_workspace_tree(project_team, ordinary_threads)
        available = tree_keys(self.tree)
        self.selected_key = previous_key if previous_key in available else None
        current_roles = {
            role.stable_key: role
            for project in self.tree.projects
            for role in project.roles
        }
        self.runtime_drafts = {
            key: value
            for key, value in self.runtime_drafts.items()
            if key in current_roles
        }
        project_ids = {project.project_id for project in self.tree.projects}
        self.project_expanded = {
            project_id: expanded
            for project_id, expanded in self.project_expanded.items()
            if project_id in project_ids
        }
        return self.tree

    def select(self, stable_key: str | None) -> None:
        self.selected_key = stable_key if stable_key in tree_keys(self.tree) else None

    def selected_chat_thread_id(self) -> str | None:
        selected = find_tree_item(self.tree, self.selected_key)
        if isinstance(selected, RoleViewModel):
            return selected.thread_id if selected.can_open else None
        if isinstance(selected, OrdinaryThreadViewModel):
            return selected.thread_id
        return None

    def set_runtime_draft(
        self,
        stable_key: str,
        draft: RoleRuntimeDraft,
    ) -> None:
        selected = find_tree_item(self.tree, stable_key)
        if not isinstance(selected, RoleViewModel) or not selected.can_set_runtime:
            raise ValueError("runtime draft requires a writable project role")
        self.runtime_drafts[stable_key] = draft

    def runtime_draft_for(self, role: RoleViewModel) -> RoleRuntimeDraft:
        return self.runtime_drafts.get(role.stable_key, role.runtime)


def normalize_workspace_tree(
    project_team: Any,
    ordinary_threads: Any = None,
) -> WorkspaceTreeViewModel:
    snapshot = project_team if isinstance(project_team, Mapping) else {}
    schema = snapshot.get("schema")
    if schema not in {None, "hia-project-team/1", "hia-project-team/2"}:
        return WorkspaceTreeViewModel(state_status="unsupported")

    projects = tuple(
        project
        for raw in _mapping_list(snapshot.get("projects"))
        if (project := _normalize_project(raw)) is not None
    )
    role_ids = {
        role.thread_id for project in projects for role in project.roles
    }
    ordinary = tuple(
        thread
        for raw in _thread_candidates(ordinary_threads)
        if (thread := _normalize_ordinary_thread(raw)) is not None
        and thread.thread_id not in role_ids
    )
    settings = snapshot.get("settings")
    mode = settings.get("mode") if isinstance(settings, Mapping) else None
    state_status = _text(snapshot.get("state_status"), limit=64) or "ready"
    return WorkspaceTreeViewModel(
        projects=projects,
        ordinary_threads=ordinary,
        default_route=mode if mode in {"single", "team"} else "single",
        state_status=state_status,
    )


def _normalize_project(raw: Mapping[str, Any]) -> ProjectViewModel | None:
    project_id = _text(raw.get("project_id"), limit=256)
    if not project_id:
        return None
    status = _text(raw.get("status"), limit=64) or "unknown"
    roles = tuple(
        role
        for role_name in ROLE_ORDER
        for role in [_normalize_role(project_id, role_name, raw.get("threads"))]
        if role is not None
    )
    actions = raw.get("actions")
    latest_ids = tuple(
        _text(value, limit=256)
        for value in raw.get("latest_evidence_ids", [])
        if _text(value, limit=256)
    ) if isinstance(raw.get("latest_evidence_ids"), list) else ()
    consumed = raw.get("consumed_turns")
    attention = AttentionViewModel(
        visible=status == "needs_attention",
        reason=_optional_text(raw.get("attention_reason"), limit=1200),
        stage=_optional_text(raw.get("stage"), limit=256),
        consumed_turns=(
            consumed
            if isinstance(consumed, int) and not isinstance(consumed, bool) and consumed >= 0
            else 0
        ),
        last_error=_optional_text(raw.get("last_error"), limit=1200),
        latest_evidence_ids=latest_ids,
        latest_repair_card=_optional_text(raw.get("latest_repair_card"), limit=2400),
    )
    return ProjectViewModel(
        project_id=project_id,
        title=_text(raw.get("title"), limit=240) or "未命名项目",
        status=status,
        stage=_optional_text(raw.get("stage"), limit=256),
        roles=roles,
        requirements=tuple(
            RequirementViewModel(
                requirement_id=_text(item.get("requirement_id"), limit=256),
                kind=_text(item.get("kind"), limit=120),
                status=_text(item.get("status"), limit=64),
            )
            for item in _mapping_list(raw.get("requirements"))
            if _text(item.get("requirement_id"), limit=256)
        ),
        attention=attention,
        can_continue=_boolean_action(actions, "continue"),
        can_guide=_boolean_action(actions, "append_guidance"),
        can_stop=_boolean_action(actions, "stop"),
    )


def _normalize_role(
    project_id: str,
    role_name: str,
    values: Any,
) -> RoleViewModel | None:
    for raw in _mapping_list(values):
        if raw.get("role") != role_name:
            continue
        thread_id = _text(raw.get("thread_id"), limit=256)
        if not thread_id:
            return None
        actions = raw.get("actions")
        return RoleViewModel(
            project_id=project_id,
            role=role_name,
            title=_text(raw.get("role_title"), limit=120) or ROLE_TITLES[role_name],
            thread_id=thread_id,
            status=_text(raw.get("status"), limit=64) or "waiting",
            runtime=RoleRuntimeDraft(
                _optional_text(raw.get("model"), limit=256),
                _optional_text(raw.get("effort"), limit=64),
                _optional_text(raw.get("service_tier"), limit=64),
            ),
            can_open=_boolean_action(actions, "open_thread"),
            can_guide=_boolean_action(actions, "append_guidance"),
            can_set_runtime=_boolean_action(actions, "set_role_runtime"),
        )
    return None


def _normalize_ordinary_thread(raw: Mapping[str, Any]) -> OrdinaryThreadViewModel | None:
    thread_id = _text(raw.get("thread_id"), limit=256)
    if not thread_id:
        return None
    updated = raw.get("recency_at", raw.get("updated_at", 0))
    if not isinstance(updated, int) or isinstance(updated, bool) or updated < 0:
        updated = 0
    preview = _text(raw.get("preview"), limit=320)
    return OrdinaryThreadViewModel(
        thread_id=thread_id,
        title=_text(raw.get("name"), limit=240) or preview or "未命名任务",
        preview=preview,
        updated_at=updated,
    )


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _thread_candidates(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        value = value.get("threads")
    return _mapping_list(value)


def tree_keys(tree: WorkspaceTreeViewModel) -> frozenset[str]:
    return frozenset(
        [project.stable_key for project in tree.projects]
        + [role.stable_key for project in tree.projects for role in project.roles]
        + [thread.stable_key for thread in tree.ordinary_threads]
    )


def find_tree_item(
    tree: WorkspaceTreeViewModel,
    stable_key: str | None,
) -> ProjectViewModel | RoleViewModel | OrdinaryThreadViewModel | None:
    if stable_key is None:
        return None
    for project in tree.projects:
        if project.stable_key == stable_key:
            return project
        for role in project.roles:
            if role.stable_key == stable_key:
                return role
    for thread in tree.ordinary_threads:
        if thread.stable_key == stable_key:
            return thread
    return None


def update_role_runtime(
    role: RoleViewModel,
    draft: RoleRuntimeDraft,
) -> RoleViewModel:
    """Return the acknowledged role model without mutating other roles."""

    return replace(role, runtime=draft)
