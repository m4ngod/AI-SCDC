from pydantic import BaseModel, Field


class MockWorkerResult(BaseModel):
    task_id: str
    transitions: list[str]
    patch_result: dict
    review_result: dict
    files_changed: list[str] = Field(default_factory=list)


class MockWorkerSimulator:
    def simulate(self, task_id: str, role_required: str) -> MockWorkerResult:
        return MockWorkerResult(
            task_id=task_id,
            transitions=[
                "ASSIGNED",
                "IN_PROGRESS",
                "PATCH_READY",
                "REVIEWING",
            ],
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
