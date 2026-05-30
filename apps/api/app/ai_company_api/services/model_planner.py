import json
from dataclasses import dataclass

from pydantic import ValidationError

from ai_company_api.models.entities import Project
from ai_company_api.services.planner import TaskSpecDraft
from ai_company_llm_gateway.models import ChatMessage


class ModelPlannerError(RuntimeError):
    """Raised when model planner output cannot be used."""


@dataclass(frozen=True)
class PlannerExecutionResult:
    task_specs: list[TaskSpecDraft]
    planner_kind: str
    model_route_id: str | None = None
    model_provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None


def build_planner_messages(goal: str, project_name: str) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content=(
                "You are the planner for AI Software Company Desktop Console. "
                "Return JSON only: an array of task draft objects. Each object "
                "must include title, role_required, objective, acceptance_criteria, "
                "allowed_paths, required_tests, and risk_level. role_required must "
                "be one of planner, frontend, backend, reviewer, debugger, security, "
                "product, documentation. risk_level must be low, medium, or high. "
                "Do not include Markdown or explanatory text."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Project: {project_name}\n"
                f"Goal: {goal}\n"
                "Create 2 to 5 implementation task drafts for human approval."
            ),
        ),
    ]


def parse_task_spec_drafts(content: str) -> list[TaskSpecDraft]:
    raw_json = _strip_json_fence(content.strip())
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ModelPlannerError("Model output is not valid JSON") from exc
    if not isinstance(payload, list) or len(payload) == 0:
        raise ModelPlannerError("Model output must be a non-empty JSON array")
    try:
        return [TaskSpecDraft.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ModelPlannerError("Model output does not match TaskSpec schema") from exc


def _strip_json_fence(content: str) -> str:
    if content.startswith("```json") and content.endswith("```"):
        return content.removeprefix("```json").removesuffix("```").strip()
    if content.startswith("```") and content.endswith("```"):
        return content.removeprefix("```").removesuffix("```").strip()
    return content


def project_name(project: Project) -> str:
    return project.name
