import json
from dataclasses import dataclass

from pydantic import ValidationError

from ai_company_api.models.entities import Project
from ai_company_api.schemas.api import AgentRole, RiskLevel
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
    role_values = ", ".join(role.value for role in AgentRole)
    risk_level_values = ", ".join(risk_level.value for risk_level in RiskLevel)
    return [
        ChatMessage(
            role="system",
            content=(
                "You are the planner for AI Software Company Desktop Console. "
                "Return JSON only: an array of task draft objects. Each object "
                "must include title, role_required, objective, acceptance_criteria, "
                "allowed_paths, required_tests, and risk_level. role_required must "
                f"be one of {role_values}. risk_level must be one of "
                f"{risk_level_values}. "
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
    lines = content.splitlines()
    if len(lines) < 2:
        return content

    opening_fence = lines[0].strip()
    closing_fence = lines[-1].strip()
    if not opening_fence.startswith("```") or closing_fence != "```":
        return content

    fence_info = opening_fence.removeprefix("```").strip().lower()
    if fence_info not in {"", "json"}:
        return content

    return "\n".join(lines[1:-1]).strip()


def project_name(project: Project) -> str:
    return project.name
