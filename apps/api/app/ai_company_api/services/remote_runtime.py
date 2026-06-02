from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from sqlmodel import Session

from ai_company_api.services.object_storage import (
    ObjectStorageWrite,
    get_object_storage_provider,
)


class RemoteRuntimeProviderNotFound(Exception):
    pass


class RemoteRuntimeProvider(Protocol):
    name: str

    def submit(
        self,
        session: Session,
        submission: "RemoteRuntimeSubmission",
    ) -> "RemoteRuntimeSubmissionResult":
        ...


@dataclass(frozen=True)
class RemoteRuntimeSubmission:
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    queue_provider: str
    runtime_provider: str
    storage_provider: str | None
    status: str


@dataclass(frozen=True)
class RemoteRuntimeSubmissionResult:
    runtime_job_id: str
    external_status: str
    artifact_manifest_uri: str | None = None
    log_stream_uri: str | None = None


class RemoteStubRuntimeProvider:
    name = "remote_stub"

    def submit(
        self,
        session: Session,
        submission: RemoteRuntimeSubmission,
    ) -> RemoteRuntimeSubmissionResult:
        artifact_manifest_uri: str | None = None
        log_stream_uri: str | None = None

        if submission.storage_provider == "local_inline":
            storage_provider = get_object_storage_provider("local_inline")
            manifest_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="manifest",
                    content=json.dumps(
                        {
                            "cloud_run_id": submission.cloud_run_id,
                            "queue_provider": submission.queue_provider,
                            "runtime_provider": submission.runtime_provider,
                            "storage_provider": submission.storage_provider,
                            "status": "submitted",
                        },
                        sort_keys=True,
                    ),
                    content_type="application/json",
                ),
            )
            log_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="log",
                    content="Remote runtime submitted via remote_stub.\n",
                    content_type="text/plain",
                ),
            )
            artifact_manifest_uri = manifest_ref.uri
            log_stream_uri = log_ref.uri

        return RemoteRuntimeSubmissionResult(
            runtime_job_id=f"remote-stub-job-{submission.cloud_run_id}",
            external_status="submitted",
            artifact_manifest_uri=artifact_manifest_uri,
            log_stream_uri=log_stream_uri,
        )


_KNOWN_RUNTIME_PROVIDERS = {
    "remote_stub": RemoteStubRuntimeProvider(),
}


def get_remote_runtime_provider(name: str | None) -> RemoteRuntimeProvider | None:
    if name is None:
        return None
    provider = _KNOWN_RUNTIME_PROVIDERS.get(name)
    if provider is None:
        raise RemoteRuntimeProviderNotFound(
            f"Unknown remote runtime provider: {name}"
        )
    return provider
