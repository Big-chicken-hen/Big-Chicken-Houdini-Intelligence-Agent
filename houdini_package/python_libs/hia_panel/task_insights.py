"""Bounded public task-insight models for the native Houdini Panel."""

from __future__ import annotations

from typing import Any

from .runtime_diagnostics import RuntimeDiagnosticWriter


_BRIEF_TEXT_LIMIT = 2_000
_LIST_ITEM_LIMIT = 320
_STAGE_LIMIT = 32
_REVIEW_LIMIT = 24
_REVIEW_FIELD_LIMIT = 1_200


def bounded_public_text(value: Any, limit: int) -> str:
    """Return one display-safe public string without serializing raw payloads."""

    if not isinstance(value, str):
        return ""
    cleaned = "".join(
        " " if ord(character) < 32 and character not in "\n\t" else character
        for character in value
    ).strip()
    return RuntimeDiagnosticWriter._sanitize_text(cleaned, limit)


def _first_text(payload: dict[str, Any], *keys: str, limit: int) -> str:
    for key in keys:
        value = bounded_public_text(payload.get(key), limit)
        if value:
            return value
    return ""


def _text_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    rendered: list[str] = []
    for item in value:
        text = bounded_public_text(item, _LIST_ITEM_LIMIT)
        if text:
            rendered.append(text)
    return tuple(rendered[:12])


def normalize_build_brief(value: Any) -> dict[str, Any] | None:
    """Normalize an explicitly supplied Build Brief without inventing content."""

    if not isinstance(value, dict):
        return None
    title = _first_text(value, "title", "name", limit=160)
    summary = _first_text(
        value,
        "summary",
        "objective",
        "brief",
        limit=_BRIEF_TEXT_LIMIT,
    )
    constraints = _text_list(value.get("constraints"))
    deliverables = _text_list(value.get("deliverables") or value.get("outputs"))
    if not any((title, summary, constraints, deliverables)):
        return None
    return {
        "title": title,
        "summary": summary,
        "constraints": constraints,
        "deliverables": deliverables,
        "source": _first_text(value, "source", limit=120),
        "ready": True,
    }


def normalize_stage_plan(value: Any) -> list[dict[str, str]]:
    """Project native public plan steps into a bounded stage list."""

    if not isinstance(value, list):
        return []
    stages: list[dict[str, str]] = []
    for raw_stage in value[:_STAGE_LIMIT]:
        if not isinstance(raw_stage, dict):
            continue
        title = _first_text(raw_stage, "step", "title", limit=320)
        if not title:
            continue
        stages.append(
            {
                "title": title,
                "status": _first_text(raw_stage, "status", limit=64) or "unknown",
                "summary": _first_text(
                    raw_stage,
                    "summary",
                    "details",
                    limit=600,
                ),
            }
        )
    return stages


def normalize_context_pack_summary(value: Any) -> dict[str, Any] | None:
    """Keep only public Context Pack metadata, never indexed bodies or chunks."""

    if not isinstance(value, dict):
        return None
    title = _first_text(value, "title", "name", limit=160)
    source = _first_text(value, "source", limit=200)
    raw_count = value.get("itemCount", value.get("count"))
    count = (
        raw_count
        if isinstance(raw_count, int) and not isinstance(raw_count, bool)
        and 0 <= raw_count <= 100_000
        else None
    )
    if not title and not source and count is None:
        return None
    return {"title": title, "source": source, "count": count}


def normalize_reviews(value: Any) -> list[dict[str, str]]:
    """Normalize explicit professional-review records into uniform public fields."""

    candidates = value if isinstance(value, list) else [value]
    reviews: list[dict[str, str]] = []
    for raw_review in candidates[:_REVIEW_LIMIT]:
        if not isinstance(raw_review, dict):
            continue
        review = {
            "domain": _first_text(
                raw_review,
                "domain",
                limit=120,
            ),
            "severity": _first_text(
                raw_review,
                "severity",
                limit=80,
            ),
            "object_path": _first_text(
                raw_review,
                "object",
                "objectPath",
                "path",
                limit=512,
            ),
            "evidence": _first_text(
                raw_review,
                "evidence",
                limit=_REVIEW_FIELD_LIMIT,
            ),
            "next_action": _first_text(
                raw_review,
                "suggestedNextAction",
                "suggested_next_action",
                "nextAction",
                limit=_REVIEW_FIELD_LIMIT,
            ),
        }
        if any(review.values()):
            reviews.append(review)
    return reviews
