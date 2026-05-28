import pytest

from ai_company_worker.simulator import (
    MockWorkerSimulator,
    validate_transitions_against_api_state_machine,
)
from ai_company_api.services.task_state import InvalidTaskTransition
from ai_company_api.services.task_state import TaskStatus, validate_transition


def test_simulate_frontend_task_returns_mock_progress_and_results():
    result = MockWorkerSimulator().simulate(
        task_id="task_123",
        role_required="frontend",
    )

    assert result.task_id == "task_123"
    assert result.transitions == [
        "ASSIGNED",
        "IN_PROGRESS",
        "PATCH_READY",
        "REVIEWING",
    ]
    assert result.patch_result["status"] == "patch_ready"
    assert result.review_result["verdict"] == "approved"


def test_simulate_backend_task_does_not_change_files():
    result = MockWorkerSimulator().simulate(
        task_id="task_123",
        role_required="backend",
    )

    assert result.files_changed == []


def test_simulator_transition_sequence_is_valid_against_api_state_machine():
    current_status = TaskStatus.CREATED

    for requested_status in MockWorkerSimulator().simulate(
        task_id="task_123",
        role_required="frontend",
    ).transitions:
        current_status = validate_transition(
            current_status,
            TaskStatus(requested_status),
            actor_type="system",
        )

    assert current_status == TaskStatus.REVIEWING


def test_simulator_validation_fails_when_sequence_drifts():
    with pytest.raises(InvalidTaskTransition):
        validate_transitions_against_api_state_machine(["PATCH_READY"])
