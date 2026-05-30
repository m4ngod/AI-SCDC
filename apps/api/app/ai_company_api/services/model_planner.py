import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlmodel import Session

from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
    ModelProviderType,
    ModelRoute,
    Project,
)
from ai_company_api.schemas.api import AgentRole, RiskLevel, UsageLedgerCreate
from ai_company_api.services.model_settings import resolve_model_route
from ai_company_api.services.planner import TaskSpecDraft
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault
from ai_company_api.services.usage_ledger import append_usage_ledger_entry
from ai_company_llm_gateway.models import (
    ChatMessage,
    ChatProviderRequest,
    ProviderGatewayError,
)
from ai_company_llm_gateway.openai_compatible import OpenAICompatibleChatAdapter


AdapterFactory = Callable[..., object]


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


def create_model_planner_result(
    session: Session,
    project: Project,
    goal: str,
    planner_run_id: str,
    adapter_factory: AdapterFactory = OpenAICompatibleChatAdapter,
    vault: SecretVault | None = None,
) -> PlannerExecutionResult:
    resolved = resolve_model_route(session, AgentRole.PLANNER)
    if resolved.resolution_source == "fallback_fake":
        return _fake_result("no_configured_route")
    if resolved.provider_type == ModelProviderType.FAKE.value:
        return _fake_result(None)
    if not resolved.is_available or resolved.route_id is None:
        return _fake_result(_unavailable_reason(resolved))

    route = session.get(ModelRoute, resolved.route_id)
    if route is None:
        return _fake_result("no_configured_route")
    provider = session.get(ModelProvider, route.provider_id)
    if provider is None or _enum_value(provider.status) != ModelProviderStatus.ACTIVE.value:
        return _fake_result("provider_unavailable")
    credential = _active_credential(session, route.credential_id)
    if credential is None:
        return _fake_result("credential_unavailable")

    try:
        secret = (vault or DevSecretVault()).open(credential.encrypted_secret)
    except ValueError:
        return _fake_result("credential_unavailable")

    try:
        adapter = adapter_factory(
            provider_name=provider.name,
            base_url=provider.base_url or "",
            api_key=secret,
        )
        response = adapter.complete_chat(
            ChatProviderRequest(
                model_name=route.model_name,
                messages=build_planner_messages(
                    goal=goal,
                    project_name=project_name(project),
                ),
            )
        )
    except ProviderGatewayError:
        return _fake_result("provider_request_failed")

    try:
        task_specs = parse_task_spec_drafts(response.content)
    except ModelPlannerError:
        return _fake_result("invalid_model_output")

    append_usage_ledger_entry(
        session,
        UsageLedgerCreate(
            project_id=project.id,
            planner_run_id=planner_run_id,
            provider_name=response.provider_name,
            model_name=response.model_name,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            raw_usage_json={
                "source": "model_planner",
                "route_id": route.id,
            },
        ),
        commit=False,
    )

    return PlannerExecutionResult(
        task_specs=task_specs,
        planner_kind="model",
        model_route_id=route.id,
        model_provider_name=provider.name,
        model_name=route.model_name,
        fallback_reason=None,
    )


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


def _fake_result(fallback_reason: str | None) -> PlannerExecutionResult:
    return PlannerExecutionResult(
        task_specs=[],
        planner_kind="fake" if fallback_reason is None else "model_fallback_fake",
        fallback_reason=fallback_reason,
    )


def _active_credential(
    session: Session,
    credential_id: str | None,
) -> ModelCredential | None:
    if credential_id is None:
        return None
    credential = session.get(ModelCredential, credential_id)
    if credential is None:
        return None
    if _enum_value(credential.status) != ModelCredentialStatus.ACTIVE.value:
        return None
    return credential


def _unavailable_reason(resolved) -> str:
    if resolved.provider_type != ModelProviderType.FAKE.value and not resolved.credential_available:
        return "credential_unavailable"
    return "provider_unavailable"


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


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
