from ai_company_worker.simulator import MockWorkerSimulator


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
