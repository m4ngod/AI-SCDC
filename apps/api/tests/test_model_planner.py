import pytest
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
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
    ProviderRequestError,
    UsageRecord,
)


class RecordingChatAdapter:
    def __init__(self, response: ChatProviderResponse) -> None:
        self.response = response
        self.requests = []

    def complete_chat(self, request):
        self.requests.append(request)
        return self.response


class ClosingChatAdapter(RecordingChatAdapter):
    def __init__(self, response: ChatProviderResponse) -> None:
        super().__init__(response)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FailingClosingChatAdapter:
    def __init__(self) -> None:
        self.closed = False

    def complete_chat(self, _request):
        raise ProviderRequestError("provider down")

    def close(self) -> None:
        self.closed = True


class RaisingCloseChatAdapter(RecordingChatAdapter):
    def __init__(self, response: ChatProviderResponse) -> None:
        super().__init__(response)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        raise RuntimeError("close failed")


class FailingRaisingCloseChatAdapter:
    def __init__(self) -> None:
        self.closed = False

    def complete_chat(self, _request):
        raise ProviderRequestError("provider down")

    def close(self) -> None:
        self.closed = True
        raise RuntimeError("close failed")


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


def test_create_model_planner_result_closes_adapter_after_success() -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_closes_adapter",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = ClosingChatAdapter(
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

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **_kwargs: adapter,
        )

    assert result.planner_kind == "model"
    assert adapter.closed is True


def test_create_model_planner_result_closes_adapter_after_provider_failure() -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_provider_failure_closes_adapter",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = FailingClosingChatAdapter()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **_kwargs: adapter,
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_request_failed"
    assert adapter.closed is True


def test_create_model_planner_result_suppresses_close_failure_after_success() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_close_failure_success",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = RaisingCloseChatAdapter(
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

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **_kwargs: adapter,
        )
        usage_entries = list_usage_ledger_entries(
            session,
            planner_run_id=planner_run.id,
        )

    assert result.planner_kind == "model"
    assert result.model_route_id == route.id
    assert result.fallback_reason is None
    assert adapter.closed is True
    assert usage_entries[0].total_tokens == 48


def test_create_model_planner_result_suppresses_close_failure_after_provider_error() -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_close_failure_provider_error",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = FailingRaisingCloseChatAdapter()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **_kwargs: adapter,
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_request_failed"
    assert adapter.closed is True


def test_model_planner_falls_back_when_no_route_is_configured() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        session.add(project)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "no_configured_route"
    assert result.task_specs == []


def test_model_planner_falls_back_when_credential_is_deleted() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        credential = session.get(ModelCredential, route.credential_id)
        assert credential is not None
        credential.status = ModelCredentialStatus.DELETED
        session.add(credential)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "credential_unavailable"
    assert result.task_specs == []


def test_model_planner_falls_back_when_provider_is_disabled() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        provider = session.get(ModelProvider, route.provider_id)
        assert provider is not None
        provider.status = ModelProviderStatus.DISABLED
        session.add(provider)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_unavailable"
    assert result.task_specs == []


def test_model_planner_falls_back_when_provider_request_fails() -> None:
    class FailingAdapter:
        def complete_chat(self, _request):
            raise ProviderRequestError("provider down")

    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_manual",
            project_id=project.id,
            goal="Build planner",
        )
        session.add(planner_run)
        session.flush()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: FailingAdapter(),
        )
        usage = list_usage_ledger_entries(session, planner_run_id="planner_run_manual")

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_request_failed"
    assert result.task_specs == []
    assert usage == []


def test_model_planner_falls_back_when_model_output_is_invalid() -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_manual",
            project_id=project.id,
            goal="Build planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = RecordingChatAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="not json",
                usage=UsageRecord(prompt_tokens=3, completion_tokens=4),
            )
        )

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: adapter,
        )
        usage = list_usage_ledger_entries(session, planner_run_id="planner_run_manual")

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "invalid_model_output"
    assert result.task_specs == []
    assert usage == []


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
