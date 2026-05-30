import pytest
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import (
    ModelCredential,
    ModelProvider,
    ModelProviderType,
    ModelRoute,
    PlannerRun,
    Project,
)
from ai_company_api.schemas.api import AgentRole, RiskLevel
from ai_company_api.services.model_planner import (
    ModelPlannerError,
    build_planner_messages,
    create_model_planner_result,
    parse_task_spec_drafts,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.usage_ledger import list_usage_ledger_entries
from ai_company_llm_gateway.models import (
    ChatProviderResponse,
    UsageRecord,
)


class RecordingChatAdapter:
    def __init__(self, response: ChatProviderResponse) -> None:
        self.response = response
        self.requests = []

    def complete_chat(self, request):
        self.requests.append(request)
        return self.response


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def create_planner_route(session: Session) -> tuple[Project, ModelRoute]:
    vault = DevSecretVault()
    sealed = vault.seal("sk-example1234")
    project = Project(name="Demo Project")
    provider = ModelProvider(
        name="deepseek-dev",
        provider_type=ModelProviderType.DEEPSEEK,
        base_url="https://api.deepseek.com",
    )
    credential = ModelCredential(
        provider_id=provider.id,
        display_name="DeepSeek key",
        secret_last4=sealed.secret_last4,
        encrypted_secret=sealed.encrypted_secret,
    )
    route = ModelRoute(
        agent_role="planner",
        provider_id=provider.id,
        credential_id=credential.id,
        model_name="deepseek-chat",
    )
    session.add(project)
    session.add(provider)
    session.add(credential)
    session.add(route)
    session.commit()
    session.refresh(project)
    session.refresh(route)
    return project, route


def test_create_model_planner_result_uses_configured_route_and_logs_usage() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_manual",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = RecordingChatAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="""
                [
                  {
                    "title": "Implement API planner integration",
                    "role_required": "backend",
                    "objective": "Use configured route for planner drafts.",
                    "acceptance_criteria": ["Model drafts are persisted."],
                    "allowed_paths": ["apps/api/**"],
                    "required_tests": ["pytest apps/api/tests/test_model_planner.py -v"],
                    "risk_level": "medium"
                  }
                ]
                """,
                usage=UsageRecord(prompt_tokens=31, completion_tokens=17),
            )
        )
        adapter_kwargs = {}

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **kwargs: adapter_kwargs.update(kwargs) or adapter,
        )
        usage_entries = list_usage_ledger_entries(
            session,
            planner_run_id="planner_run_manual",
        )

    assert adapter_kwargs == {
        "provider_name": "deepseek-dev",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-example1234",
    }
    assert result.planner_kind == "model"
    assert result.model_route_id == route.id
    assert result.model_provider_name == "deepseek-dev"
    assert result.model_name == "deepseek-chat"
    assert result.fallback_reason is None
    assert result.task_specs[0].title == "Implement API planner integration"
    assert adapter.requests[0].model_name == "deepseek-chat"
    assert usage_entries[0].provider_name == "deepseek-dev"
    assert usage_entries[0].model_name == "deepseek-chat"
    assert usage_entries[0].prompt_tokens == 31
    assert usage_entries[0].completion_tokens == 17
    assert usage_entries[0].total_tokens == 48


def test_create_model_planner_result_propagates_usage_ledger_failures(
    monkeypatch,
) -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_usage_failure",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = RecordingChatAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="""
                [
                  {
                    "title": "Implement API planner integration",
                    "role_required": "backend",
                    "objective": "Use configured route for planner drafts.",
                    "acceptance_criteria": ["Model drafts are persisted."],
                    "allowed_paths": ["apps/api/**"],
                    "required_tests": ["pytest apps/api/tests/test_model_planner.py -v"],
                    "risk_level": "medium"
                  }
                ]
                """,
                usage=UsageRecord(prompt_tokens=31, completion_tokens=17),
            )
        )

        def fail_usage_append(*_args, **_kwargs):
            raise RuntimeError("usage ledger unavailable")

        monkeypatch.setattr(
            "ai_company_api.services.model_planner.append_usage_ledger_entry",
            fail_usage_append,
        )

        with pytest.raises(RuntimeError, match="usage ledger unavailable"):
            create_model_planner_result(
                session,
                project=project,
                goal="Build real planner",
                planner_run_id=planner_run.id,
                adapter_factory=lambda **_kwargs: adapter,
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


def test_build_planner_messages_includes_current_schema_values() -> None:
    messages = build_planner_messages(
        goal="Build real planner",
        project_name="Demo Project",
    )
    system_content = messages[0].content

    for role in AgentRole:
        assert role.value in system_content
    for risk_level in RiskLevel:
        assert risk_level.value in system_content


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


def test_parse_task_spec_drafts_unwraps_variant_markdown_json_fence() -> None:
    drafts = parse_task_spec_drafts(
        """``` JSON
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

        ```
        """
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
