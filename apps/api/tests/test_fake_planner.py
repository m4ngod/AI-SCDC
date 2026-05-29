import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_company_api.services.planner import FakePlanner, TaskSpecDraft


def test_fake_planner_returns_deterministic_task_spec_drafts() -> None:
    planner = FakePlanner()
    first = planner.plan(
        project_id="project_123",
        goal="Build model route settings",
    )
    second = planner.plan(
        project_id="project_123",
        goal="Build model route settings",
    )

    assert first == second
    assert [draft.role_required for draft in first] == ["frontend", "backend"]
    assert all("Build model route settings" in draft.objective for draft in first)


def test_fake_planner_normalizes_goal_whitespace() -> None:
    drafts = FakePlanner().plan(
        project_id="project_123",
        goal="  Build   model\nroute settings  ",
    )

    assert all("Build model route settings" in draft.title for draft in drafts)
    assert all("Build model route settings" in draft.objective for draft in drafts)
    assert all("  Build" not in draft.title for draft in drafts)
    assert all("\n" not in draft.objective for draft in drafts)


def test_fake_planner_output_matches_agent_protocol_enums() -> None:
    role_schema = json.loads(
        Path("packages/agent-protocol/schemas/agent-role.schema.json").read_text()
    )
    task_spec_schema = json.loads(
        Path("packages/agent-protocol/schemas/task-spec.schema.json").read_text()
    )

    drafts = FakePlanner().plan(
        project_id="project_123",
        goal="Build model route settings",
    )

    assert drafts
    for draft in drafts:
        assert draft.role_required in role_schema["enum"]
        assert draft.risk_level in task_spec_schema["properties"]["risk_level"]["enum"]
        assert draft.title
        assert draft.acceptance_criteria
        assert draft.allowed_paths
        assert isinstance(draft.required_tests, list)


def test_task_spec_draft_rejects_empty_scalar_strings() -> None:
    valid_draft = _valid_task_spec_draft()

    for field_name in ("title", "objective"):
        with pytest.raises(ValidationError):
            TaskSpecDraft(**{**valid_draft, field_name: ""})


def test_task_spec_draft_rejects_empty_list_items() -> None:
    valid_draft = _valid_task_spec_draft()

    for field_name in ("acceptance_criteria", "allowed_paths", "required_tests"):
        with pytest.raises(ValidationError):
            TaskSpecDraft(**{**valid_draft, field_name: [""]})


def test_task_spec_draft_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TaskSpecDraft(**_valid_task_spec_draft(), unexpected_field="not allowed")


@pytest.mark.parametrize(
    "field_name",
    (
        "title",
        "role_required",
        "objective",
        "acceptance_criteria",
        "allowed_paths",
        "required_tests",
        "risk_level",
    ),
)
def test_task_spec_draft_rejects_missing_required_fields(field_name: str) -> None:
    draft = _valid_task_spec_draft()
    draft.pop(field_name)

    with pytest.raises(ValidationError):
        TaskSpecDraft(**draft)


def _valid_task_spec_draft() -> dict[str, object]:
    return {
        "title": "Implement planner API",
        "role_required": "backend",
        "objective": "Persist planner runs.",
        "acceptance_criteria": ["Planner run creation stores ordered drafts."],
        "allowed_paths": ["apps/api/**"],
        "required_tests": ["Planner run endpoint creates drafts"],
        "risk_level": "medium",
    }
