from __future__ import annotations

from sqlmodel import Session

from ai_company_api.models.entities import CloudRun
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.object_storage import ObjectStorageError, ObjectStorageRef
from ai_company_api.services.remote_runtime import (
    RemoteRuntimeLogSyncRequest,
    RemoteRuntimeLogSyncResult,
    RemoteRuntimeProviderNotFound,
    get_remote_runtime_provider,
)


def sync_cloud_run_log_stream(
    session: Session,
    *,
    cloud_run: CloudRun,
) -> RemoteRuntimeLogSyncResult:
    try:
        provider = get_remote_runtime_provider(cloud_run.runtime_provider)
    except RemoteRuntimeProviderNotFound:
        return RemoteRuntimeLogSyncResult(
            status="unsupported",
            reason="unknown_runtime_provider",
        )

    if provider is None:
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="missing_runtime_provider",
        )

    request = RemoteRuntimeLogSyncRequest(
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        cloud_run_id=cloud_run.id,
        runtime_job_id=cloud_run.runtime_job_id,
        storage_provider=cloud_run.storage_provider,
        current_log_stream_ref=_current_log_stream_ref(cloud_run),
    )
    try:
        result = provider.sync_logs(session, request)
    except (AliyunConfigurationError, ObjectStorageError):
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="log_sync_provider_unavailable",
        )
    except Exception:
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="log_sync_provider_failed",
        )

    if result.status == "updated" and result.log_stream_ref is not None:
        _persist_log_stream_ref(cloud_run, result.log_stream_ref)
        session.add(cloud_run)
        session.commit()
    return result


def _current_log_stream_ref(cloud_run: CloudRun) -> ObjectStorageRef | None:
    if (
        cloud_run.log_stream_uri is None
        or cloud_run.log_stream_sha256 is None
        or cloud_run.log_stream_size_bytes is None
        or cloud_run.log_stream_content_type is None
    ):
        return None
    return ObjectStorageRef(
        kind="log",
        uri=cloud_run.log_stream_uri,
        sha256=cloud_run.log_stream_sha256,
        size_bytes=cloud_run.log_stream_size_bytes,
        content_type=cloud_run.log_stream_content_type,
    )


def _persist_log_stream_ref(cloud_run: CloudRun, ref: ObjectStorageRef) -> None:
    cloud_run.log_stream_uri = ref.uri
    cloud_run.log_stream_sha256 = ref.sha256
    cloud_run.log_stream_size_bytes = ref.size_bytes
    cloud_run.log_stream_content_type = ref.content_type
