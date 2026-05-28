from pydantic import BaseModel, Field


class MockWorkerResult(BaseModel):
    task_id: str
    transitions: list[str]
    patch_result: dict
    review_result: dict
    files_changed: list[str] = Field(default_factory=list)


class MockWorkerSimulator:
    def simulate(self, task_id: str, role_required: str) -> MockWorkerResult:
        transitions = [
            "ASSIGNED",
            "IN_PROGRESS",
            "PATCH_READY",
            "REVIEWING",
        ]
        validate_transitions_against_api_state_machine(transitions)

        return MockWorkerResult(
            task_id=task_id,
            transitions=transitions,
            patch_result={
                "status": "patch_ready",
                "summary": f"Simulated {role_required} implementation for Phase 0.",
                "files_changed": [],
                "tests_run": [],
                "test_result": "not_run",
                "risks": [
                    "Mock worker does not modify repository files in Phase 0.",
                ],
            },
            review_result={
                "verdict": "approved",
                "issues": [],
                "required_changes": [],
            },
            files_changed=[],
        )


def validate_transitions_against_api_state_machine(transitions: list[str]) -> None:
    try:
        from ai_company_api.services.task_state import TaskStatus, validate_transition
    except ImportError as exc:
        raise RuntimeError("API task state machine is required for worker validation") from exc

    current_status = TaskStatus.CREATED
    for requested_status in transitions:
        current_status = validate_transition(
            current_status,
            TaskStatus(requested_status),
            actor_type="system",
        )
