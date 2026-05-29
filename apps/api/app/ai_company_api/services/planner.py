from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ai_company_api.schemas.api import AgentRole, RiskLevel


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class TaskSpecDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyString
    role_required: AgentRole
    objective: NonEmptyString
    acceptance_criteria: list[NonEmptyString] = Field(min_length=1)
    allowed_paths: list[NonEmptyString] = Field(min_length=1)
    required_tests: list[NonEmptyString] = Field(default_factory=list)
    risk_level: RiskLevel


class PlannerService(Protocol):
    def plan(self, project_id: str, goal: str) -> list[TaskSpecDraft]:
        ...


class FakePlanner:
    def plan(self, project_id: str, goal: str) -> list[TaskSpecDraft]:
        normalized_goal = " ".join(goal.split())
        return [
            TaskSpecDraft(
                title=f"Design desktop flow for {normalized_goal}",
                role_required=AgentRole.FRONTEND,
                objective=(
                    f"Create the user-facing planner approval flow for {normalized_goal}."
                ),
                acceptance_criteria=[
                    "Generated task drafts are visible in the main thread.",
                    "The user can approve or reject the generated draft batch.",
                ],
                allowed_paths=["apps/desktop/**"],
                required_tests=[
                    "App renders planner draft preview",
                    "App approval adds created tasks to the task board",
                ],
                risk_level=RiskLevel.MEDIUM,
            ),
            TaskSpecDraft(
                title=f"Implement planner API for {normalized_goal}",
                role_required=AgentRole.BACKEND,
                objective=(
                    f"Persist planner runs, task drafts, and approval decisions for {normalized_goal}."
                ),
                acceptance_criteria=[
                    "Planner run creation stores ordered drafts.",
                    "Approving a run creates normal tasks and task_created events.",
                ],
                allowed_paths=["apps/api/**"],
                required_tests=[
                    "Planner run endpoint creates drafts",
                    "Planner approval endpoint creates tasks",
                ],
                risk_level=RiskLevel.MEDIUM,
            ),
        ]
