from ai_company_worker.simulator import MockWorkerSimulator


def main() -> None:
    result = MockWorkerSimulator().simulate(
        task_id="task_dev",
        role_required="frontend",
    )
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
