import json
from pathlib import Path

from ai_company_api.services.planner import FakePlanner


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
