from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    SPEC_DRAFTED = "SPEC_DRAFTED"
    USER_APPROVED_SPEC = "USER_APPROVED_SPEC"
    TASKS_CREATED = "TASKS_CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PATCH_READY = "PATCH_READY"
    SELF_TESTING = "SELF_TESTING"
    REVIEWING = "REVIEWING"
    FIX_REQUESTED = "FIX_REQUESTED"
    APPROVED = "APPROVED"
    CI_RUNNING = "CI_RUNNING"
    MERGE_READY = "MERGE_READY"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    MERGED = "MERGED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {
    TaskStatus.MERGED,
    TaskStatus.CLOSED,
    TaskStatus.CANCELLED,
}

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {
        TaskStatus.SPEC_DRAFTED,
        TaskStatus.ASSIGNED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.SPEC_DRAFTED: {
        TaskStatus.USER_APPROVED_SPEC,
        TaskStatus.CANCELLED,
    },
    TaskStatus.USER_APPROVED_SPEC: {
        TaskStatus.TASKS_CREATED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.TASKS_CREATED: {
        TaskStatus.ASSIGNED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.ASSIGNED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.PATCH_READY,
        TaskStatus.FIX_REQUESTED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PATCH_READY: {
        TaskStatus.SELF_TESTING,
        TaskStatus.REVIEWING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.SELF_TESTING: {
        TaskStatus.REVIEWING,
        TaskStatus.FIX_REQUESTED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.REVIEWING: {
        TaskStatus.APPROVED,
        TaskStatus.FIX_REQUESTED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.FIX_REQUESTED: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
    },
    TaskStatus.APPROVED: {
        TaskStatus.CI_RUNNING,
        TaskStatus.MERGE_READY,
        TaskStatus.CANCELLED,
    },
    TaskStatus.CI_RUNNING: {
        TaskStatus.MERGE_READY,
        TaskStatus.FIX_REQUESTED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.MERGE_READY: {
        TaskStatus.HUMAN_APPROVAL,
        TaskStatus.CANCELLED,
    },
    TaskStatus.HUMAN_APPROVAL: {
        TaskStatus.MERGED,
        TaskStatus.CLOSED,
    },
    TaskStatus.MERGED: set(),
    TaskStatus.CLOSED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTaskTransition(ValueError):
    """Raised when a requested task status transition is not allowed."""


def allowed_next_statuses(current: TaskStatus) -> list[str]:
    return sorted(status.value for status in TRANSITIONS[current])


def validate_transition(
    current: TaskStatus,
    requested: TaskStatus,
    actor_type: str,
) -> TaskStatus:
    allowed = allowed_next_statuses(current)
    transition = f"{current.value} -> {requested.value}"

    if current in TERMINAL_STATUSES:
        raise InvalidTaskTransition(
            f"Invalid task transition {transition}; terminal status cannot transition; "
            f"allowed={allowed}"
        )

    if requested == TaskStatus.MERGED and (
        current != TaskStatus.HUMAN_APPROVAL or actor_type != "system"
    ):
        raise InvalidTaskTransition(
            f"Invalid task transition {transition} for actor_type={actor_type}; "
            "MERGED requires HUMAN_APPROVAL and actor_type=system; "
            f"allowed={allowed}"
        )

    if requested not in TRANSITIONS[current]:
        raise InvalidTaskTransition(
            f"Invalid task transition {transition}; allowed={allowed}"
        )

    return requested
