from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol

from sqlmodel import Session

from ai_company_api.services.aliyun_clients import (
    AliyunEciCreateContainerGroupRequest,
    get_aliyun_client_bundle,
)
from ai_company_api.services.aliyun_config import require_aliyun_settings
from ai_company_api.services.object_storage import (
    ObjectStorageWrite,
    get_object_storage_provider,
)


class RemoteRuntimeProviderNotFound(Exception):
    pass


class RemoteRuntimeSubmissionError(Exception):
    pass


class RemoteRuntimeProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        ...

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

    def validate_configuration(self) -> None:
        return None

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


class AliyunEciRuntimeProvider:
    name = "aliyun_eci"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "eci_vswitch_id",
                "eci_security_group_id",
                "eci_image",
                "api_public_base_url",
            ),
        )

    def submit(
        self,
        session: Session,
        submission: RemoteRuntimeSubmission,
    ) -> RemoteRuntimeSubmissionResult:
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "eci_vswitch_id",
                "eci_security_group_id",
                "eci_image",
                "api_public_base_url",
            ),
        )
        container_group_name = _eci_container_group_name(
            settings.eci_container_group_prefix,
            submission.cloud_run_id,
        )
        environment = {
            "AI_SCDC_API_BASE_URL": settings.api_public_base_url or "",
            "AI_SCDC_CLOUD_RUN_ID": submission.cloud_run_id,
            "AI_SCDC_WORKER_ID": f"aliyun-eci-{submission.cloud_run_id}",
            "AI_SCDC_QUEUE_PROVIDER": submission.queue_provider,
            "AI_SCDC_STORAGE_PROVIDER": submission.storage_provider or "",
        }

        runtime_job_id: str | None = None
        try:
            bundle = get_aliyun_client_bundle(settings)
            result = bundle.eci.create_container_group(
                AliyunEciCreateContainerGroupRequest(
                    region_id=settings.region_id or "",
                    cloud_run_id=submission.cloud_run_id,
                    container_group_name=container_group_name,
                    image=settings.eci_image or "",
                    vswitch_id=settings.eci_vswitch_id or "",
                    security_group_id=settings.eci_security_group_id or "",
                    cpu=settings.eci_cpu,
                    memory_gb=settings.eci_memory_gb,
                    restart_policy="Never",
                    client_token=_eci_client_token(submission.cloud_run_id),
                    environment=environment,
                    auto_create_eip=settings.eci_auto_create_eip,
                    eip_bandwidth=settings.eci_eip_bandwidth,
                )
            )
            runtime_job_id = result.get("container_group_id") or container_group_name
            artifact_manifest_uri = None
            log_stream_uri = None
            if submission.storage_provider == "aliyun_oss":
                storage_provider = get_object_storage_provider("aliyun_oss")
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
                                "runtime_job_id": runtime_job_id,
                                "runtime_provider": self.name,
                                "status": "submitted",
                                "storage_provider": submission.storage_provider,
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
                        content="Remote runtime submitted via aliyun_eci.\n",
                        content_type="text/plain",
                    ),
                )
                artifact_manifest_uri = manifest_ref.uri
                log_stream_uri = log_ref.uri
        except Exception:
            if runtime_job_id:
                try:
                    get_aliyun_client_bundle(settings).eci.delete_container_group(
                        region_id=settings.region_id or "",
                        container_group_id=runtime_job_id,
                    )
                except Exception:
                    pass
            raise RemoteRuntimeSubmissionError(
                "Cloud runtime provider aliyun_eci failed to submit container group"
            ) from None

        return RemoteRuntimeSubmissionResult(
            runtime_job_id=runtime_job_id,
            external_status="submitted",
            artifact_manifest_uri=artifact_manifest_uri,
            log_stream_uri=log_stream_uri,
        )


_KNOWN_RUNTIME_PROVIDERS = {
    "remote_stub": RemoteStubRuntimeProvider(),
    "aliyun_eci": AliyunEciRuntimeProvider(),
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


def _eci_container_group_name(prefix: str, cloud_run_id: str) -> str:
    raw_name = f"{prefix}-{cloud_run_id}"
    normalized = re.sub(r"[^A-Za-z0-9-]+", "-", raw_name).strip("-").lower()
    if not normalized:
        return "ai-scdc-run"
    if not normalized[0].isalpha():
        normalized = f"ai-scdc-run-{normalized}"
    return normalized[:128].rstrip("-")


def _eci_client_token(cloud_run_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9-]+", "-", cloud_run_id).strip("-").lower()
    if not normalized:
        normalized = "run"
    return f"ai-scdc-{normalized}"[:64].rstrip("-")
