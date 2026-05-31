import pytest

from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


def test_created_to_spec_drafted_succeeds_for_system_actor() -> None:
    assert (
        validate_transition(
            TaskStatus.CREATED,
            TaskStatus.SPEC_DRAFTED,
            actor_type="system",
        )
        == TaskStatus.SPEC_DRAFTED
    )


def test_created_to_merged_raises_with_transition_in_message() -> None:
    with pytest.raises(InvalidTaskTransition, match="CREATED -> MERGED"):
        validate_transition(
            TaskStatus.CREATED,
            TaskStatus.MERGED,
            actor_type="system",
        )


def test_only_human_approval_can_merge_for_system_actor() -> None:
    with pytest.raises(InvalidTaskTransition):
        validate_transition(
            TaskStatus.MERGE_READY,
            TaskStatus.MERGED,
            actor_type="system",
        )

    assert (
        validate_transition(
            TaskStatus.HUMAN_APPROVAL,
            TaskStatus.MERGED,
            actor_type="system",
        )
        == TaskStatus.MERGED
    )


def test_human_approval_can_create_pr_for_system_actor() -> None:
    assert (
        validate_transition(
            TaskStatus.HUMAN_APPROVAL,
            TaskStatus.PR_CREATED,
            actor_type="system",
        )
        == TaskStatus.PR_CREATED
    )


def test_pr_created_can_merge_for_system_actor() -> None:
    assert (
        validate_transition(
            TaskStatus.PR_CREATED,
            TaskStatus.MERGED,
            actor_type="system",
        )
        == TaskStatus.MERGED
    )


@pytest.mark.parametrize("actor_type", ["agent", "user"])
def test_non_system_actor_cannot_merge_from_pr_created(actor_type: str) -> None:
    with pytest.raises(InvalidTaskTransition, match=f"actor_type={actor_type}"):
        validate_transition(
            TaskStatus.PR_CREATED,
            TaskStatus.MERGED,
            actor_type=actor_type,
        )


def test_agent_cannot_merge_even_from_human_approval() -> None:
    with pytest.raises(InvalidTaskTransition, match="actor_type=agent"):
        validate_transition(
            TaskStatus.HUMAN_APPROVAL,
            TaskStatus.MERGED,
            actor_type="agent",
        )


def test_active_task_can_be_cancelled_by_user() -> None:
    assert (
        validate_transition(
            TaskStatus.IN_PROGRESS,
            TaskStatus.CANCELLED,
            actor_type="user",
        )
        == TaskStatus.CANCELLED
    )


def test_terminal_task_cannot_be_cancelled() -> None:
    with pytest.raises(InvalidTaskTransition):
        validate_transition(
            TaskStatus.MERGED,
            TaskStatus.CANCELLED,
            actor_type="user",
        )


def test_allowed_next_statuses_returns_sorted_string_values() -> None:
    assert allowed_next_statuses(TaskStatus.CREATED) == [
        "ASSIGNED",
        "CANCELLED",
        "SPEC_DRAFTED",
    ]


def test_allowed_next_statuses_includes_merged_after_pr_created() -> None:
    assert allowed_next_statuses(TaskStatus.PR_CREATED) == [
        "CANCELLED",
        "CLOSED",
        "MERGED",
    ]
