import pytest

from ai_company_api.services.model_planner import (
    ModelPlannerError,
    build_planner_messages,
    parse_task_spec_drafts,
)


def test_build_planner_messages_instructs_json_only() -> None:
    messages = build_planner_messages(
        goal="Build real planner",
        project_name="Demo Project",
    )

    assert [message.role for message in messages] == ["system", "user"]
    assert "JSON" in messages[0].content
    assert "role_required" in messages[0].content
    assert "frontend" in messages[0].content
    assert "Build real planner" in messages[1].content
    assert "Demo Project" in messages[1].content


def test_parse_task_spec_drafts_accepts_valid_json_array() -> None:
    drafts = parse_task_spec_drafts(
        """
        [
          {
            "title": "Implement model planner",
            "role_required": "backend",
            "objective": "Call a configured model route for planner drafts.",
            "acceptance_criteria": ["Model drafts are persisted."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": ["pytest apps/api/tests/test_model_planner.py -v"],
            "risk_level": "medium"
          }
        ]
        """
    )

    assert len(drafts) == 1
    assert drafts[0].title == "Implement model planner"
    assert drafts[0].role_required.value == "backend"
    assert drafts[0].risk_level.value == "medium"


def test_parse_task_spec_drafts_unwraps_markdown_json_fence() -> None:
    drafts = parse_task_spec_drafts(
        """```json
        [
          {
            "title": "Review planner output",
            "role_required": "reviewer",
            "objective": "Check generated drafts.",
            "acceptance_criteria": ["Review is complete."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": [],
            "risk_level": "low"
          }
        ]
        ```"""
    )

    assert drafts[0].role_required.value == "reviewer"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        "[]",
        '[{"title": "Missing fields"}]',
        """[
          {
            "title": "Bad role",
            "role_required": "sales",
            "objective": "No.",
            "acceptance_criteria": ["Rejected."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": [],
            "risk_level": "medium"
          }
        ]""",
    ],
)
def test_parse_task_spec_drafts_rejects_invalid_output(content: str) -> None:
    with pytest.raises(ModelPlannerError):
        parse_task_spec_drafts(content)
