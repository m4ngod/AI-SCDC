from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    CloudRun,
    CloudRunStoredObject,
    PatchArtifact,
    utc_now,
)
from ai_company_api.schemas.api import (
    ArtifactKind,
    ArtifactRetentionRead,
    CloudRunArtifactContentRead,
    CloudRunArtifactDescriptorRead,
    CloudRunArtifactManifestRead,
)
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.object_storage import (
    ObjectStorageProviderNotFound,
    ObjectStorageReadError,
    ObjectStorageRef,
    get_object_storage_provider,
)


ARTIFACT_LABELS: dict[str, str] = {
    "diff": "Unified diff",
    "log": "Log stream",
    "command_result": "Command result",
    "test_result": "Test result",
    "manifest": "Artifact manifest",
}
ARTIFACT_ORDER = {
    "diff": 0,
    "log": 1,
    "command_result": 2,
    "test_result": 3,
    "manifest": 4,
}


@dataclass(frozen=True)
class ArtifactSource:
    descriptor: CloudRunArtifactDescriptorRead
    storage_ref: ObjectStorageRef | None = None
    stored_object_id: str | None = None
    patch_artifact_id: str | None = None


def build_cloud_run_artifact_manifest(
    session: Session,
    *,
    cloud_run_id: str,
) -> CloudRunArtifactManifestRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    sources = _cloud_run_artifact_sources(session, cloud_run)
    return CloudRunArtifactManifestRead(
        version=1,
        cloud_run_id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        generated_at=utc_now(),
        retention=_retention_read(sources),
        artifacts=[source.descriptor for source in sources],
    )


def list_cloud_run_artifacts(
    session: Session,
    *,
    cloud_run_id: str,
) -> list[CloudRunArtifactDescriptorRead]:
    return build_cloud_run_artifact_manifest(
        session,
        cloud_run_id=cloud_run_id,
    ).artifacts


def get_cloud_run_artifact_descriptor(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> CloudRunArtifactDescriptorRead:
    return _get_cloud_run_artifact_source(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    ).descriptor


def read_cloud_run_artifact_content(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> CloudRunArtifactContentRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    source = _get_cloud_run_artifact_source_for_run(
        session,
        cloud_run=cloud_run,
        artifact_id=artifact_id,
    )
    _raise_if_expired(source.descriptor.expires_at)

    if source.patch_artifact_id is not None:
        content = _read_patch_artifact_diff(
            session,
            cloud_run=cloud_run,
            patch_artifact_id=source.patch_artifact_id,
        )
        return CloudRunArtifactContentRead(
            artifact=source.descriptor,
            content=content,
        )

    if source.storage_ref is None:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")

    _validate_local_inline_scope(session, cloud_run=cloud_run, source=source)
    try:
        content = get_object_storage_provider(source.descriptor.provider).read_text(
            session,
            source.storage_ref,
        )
    except (
        ObjectStorageProviderNotFound,
        ObjectStorageReadError,
        AliyunConfigurationError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CloudRunArtifactContentRead(
        artifact=source.descriptor,
        content=content,
    )


def _get_cloud_run_or_404(session: Session, cloud_run_id: str) -> CloudRun:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    return cloud_run


def _get_cloud_run_artifact_source(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> ArtifactSource:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    return _get_cloud_run_artifact_source_for_run(
        session,
        cloud_run=cloud_run,
        artifact_id=artifact_id,
    )


def _get_cloud_run_artifact_source_for_run(
    session: Session,
    *,
    cloud_run: CloudRun,
    artifact_id: str,
) -> ArtifactSource:
    for source in _cloud_run_artifact_sources(session, cloud_run):
        if source.descriptor.id == artifact_id:
            return source
    raise HTTPException(status_code=404, detail="Cloud run artifact not found")


def _cloud_run_artifact_sources(
    session: Session,
    cloud_run: CloudRun,
) -> list[ArtifactSource]:
    sources: list[ArtifactSource] = []
    seen_uris: set[str] = set()

    stored_objects = session.exec(
        select(CloudRunStoredObject)
        .where(CloudRunStoredObject.cloud_run_id == cloud_run.id)
        .where(CloudRunStoredObject.workspace_id == cloud_run.workspace_id)
        .order_by(CloudRunStoredObject.created_at, CloudRunStoredObject.id)
    ).all()
    for stored_object in stored_objects:
        source = _source_from_stored_object(cloud_run, stored_object)
        if source is None:
            continue
        sources.append(source)
        seen_uris.add(stored_object.uri)

    for source in _cloud_run_metadata_ref_sources(session, cloud_run):
        if source.descriptor.uri in seen_uris:
            continue
        sources.append(source)
        seen_uris.add(source.descriptor.uri)

    if not any(source.descriptor.kind == "diff" for source in sources):
        patch_source = _patch_artifact_diff_source(session, cloud_run)
        if patch_source is not None:
            sources.append(patch_source)

    return sorted(
        sources,
        key=lambda source: (
            ARTIFACT_ORDER.get(source.descriptor.kind, 99),
            source.descriptor.created_at.isoformat()
            if source.descriptor.created_at is not None
            else "",
            source.descriptor.id,
        ),
    )


def _source_from_stored_object(
    cloud_run: CloudRun,
    stored_object: CloudRunStoredObject,
) -> ArtifactSource | None:
    kind = _artifact_kind_or_none(stored_object.kind)
    if kind is None:
        return None
    provider = _provider_name_from_uri(stored_object.uri)
    if provider == "local_inline" and (
        _local_inline_object_id(stored_object.uri) != stored_object.id
    ):
        return None
    ref = ObjectStorageRef(
        kind=kind,
        uri=stored_object.uri,
        sha256=stored_object.sha256,
        size_bytes=stored_object.size_bytes,
        content_type=stored_object.content_type,
    )
    return ArtifactSource(
        descriptor=_descriptor_read(
            cloud_run=cloud_run,
            kind=kind,
            artifact_id=f"{kind}_{stored_object.id}",
            provider=provider,
            uri=stored_object.uri,
            sha256=stored_object.sha256,
            size_bytes=stored_object.size_bytes,
            content_type=stored_object.content_type,
            created_at=stored_object.created_at,
            expires_at=stored_object.expires_at,
            retention_policy=stored_object.retention_policy,
        ),
        storage_ref=ref,
        stored_object_id=stored_object.id,
    )


def _cloud_run_metadata_ref_sources(
    session: Session,
    cloud_run: CloudRun,
) -> list[ArtifactSource]:
    sources: list[ArtifactSource] = []
    manifest_source = _metadata_ref_source(
        session,
        cloud_run=cloud_run,
        kind="manifest",
        uri=cloud_run.artifact_manifest_uri,
        sha256_value=cloud_run.artifact_manifest_sha256,
        size_bytes=cloud_run.artifact_manifest_size_bytes,
        content_type=cloud_run.artifact_manifest_content_type,
    )
    if manifest_source is not None:
        sources.append(manifest_source)

    log_source = _metadata_ref_source(
        session,
        cloud_run=cloud_run,
        kind="log",
        uri=cloud_run.log_stream_uri,
        sha256_value=cloud_run.log_stream_sha256,
        size_bytes=cloud_run.log_stream_size_bytes,
        content_type=cloud_run.log_stream_content_type,
    )
    if log_source is not None:
        sources.append(log_source)
    return sources


def _metadata_ref_source(
    session: Session,
    *,
    cloud_run: CloudRun,
    kind: ArtifactKind,
    uri: str | None,
    sha256_value: str | None,
    size_bytes: int | None,
    content_type: str | None,
) -> ArtifactSource | None:
    if (
        uri is None
        or sha256_value is None
        or size_bytes is None
        or content_type is None
    ):
        return None

    if _is_local_inline_uri(uri):
        stored_object = _local_inline_stored_object(session, uri)
        if (
            stored_object is None
            or stored_object.workspace_id != cloud_run.workspace_id
            or stored_object.cloud_run_id != cloud_run.id
            or stored_object.kind != kind
        ):
            return None
        return _source_from_stored_object(cloud_run, stored_object)

    ref = ObjectStorageRef(
        kind=kind,
        uri=uri,
        sha256=sha256_value,
        size_bytes=size_bytes,
        content_type=content_type,
    )
    return ArtifactSource(
        descriptor=_descriptor_read(
            cloud_run=cloud_run,
            kind=kind,
            artifact_id=_stable_artifact_id(kind, uri, sha256_value),
            provider=_provider_name_from_uri(uri),
            uri=uri,
            sha256=sha256_value,
            size_bytes=size_bytes,
            content_type=content_type,
            created_at=None,
            expires_at=None,
            retention_policy=None,
        ),
        storage_ref=ref,
    )


def _patch_artifact_diff_source(
    session: Session,
    cloud_run: CloudRun,
) -> ArtifactSource | None:
    if cloud_run.patch_artifact_id is None:
        return None
    patch_artifact = session.get(PatchArtifact, cloud_run.patch_artifact_id)
    if patch_artifact is None:
        return None
    if patch_artifact.workspace_id != cloud_run.workspace_id:
        return None
    if patch_artifact.task_id != cloud_run.task_id:
        return None

    content_bytes = patch_artifact.diff_text.encode("utf-8")
    return ArtifactSource(
        descriptor=_descriptor_read(
            cloud_run=cloud_run,
            kind="diff",
            artifact_id=f"diff_{patch_artifact.id}",
            provider="patch_artifact",
            uri=f"patch-artifact://{patch_artifact.id}/diff",
            sha256=sha256(content_bytes).hexdigest(),
            size_bytes=len(content_bytes),
            content_type="text/x-diff",
            created_at=patch_artifact.created_at,
            expires_at=None,
            retention_policy=None,
        ),
        patch_artifact_id=patch_artifact.id,
    )


def _descriptor_read(
    *,
    cloud_run: CloudRun,
    kind: ArtifactKind,
    artifact_id: str,
    provider: str,
    uri: str,
    sha256: str,
    size_bytes: int,
    content_type: str,
    created_at: datetime | None,
    expires_at: datetime | None,
    retention_policy: str | None,
) -> CloudRunArtifactDescriptorRead:
    redacted_uri = _redact_uri(uri)
    return CloudRunArtifactDescriptorRead(
        id=artifact_id,
        cloud_run_id=cloud_run.id,
        kind=kind,
        label=ARTIFACT_LABELS[kind],
        provider=provider,
        uri=redacted_uri,
        redacted_uri=redacted_uri,
        sha256=sha256,
        size_bytes=size_bytes,
        content_type=content_type,
        created_at=created_at,
        expires_at=expires_at,
        retention_policy=retention_policy,
        download_url=f"/cloud-runs/{cloud_run.id}/artifacts/{artifact_id}/content",
    )


def _retention_read(sources: list[ArtifactSource]) -> ArtifactRetentionRead:
    expires_at_values = [
        source.descriptor.expires_at
        for source in sources
        if source.descriptor.expires_at is not None
    ]
    retention_policy = next(
        (
            source.descriptor.retention_policy
            for source in sources
            if source.descriptor.retention_policy is not None
        ),
        None,
    )
    return ArtifactRetentionRead(
        policy=retention_policy or "unspecified",
        expires_at=min(expires_at_values) if expires_at_values else None,
        cleanup_supported=any(
            source.descriptor.provider == "local_inline" for source in sources
        ),
    )


def _read_patch_artifact_diff(
    session: Session,
    *,
    cloud_run: CloudRun,
    patch_artifact_id: str,
) -> str:
    patch_artifact = session.get(PatchArtifact, patch_artifact_id)
    if patch_artifact is None:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if patch_artifact.id != cloud_run.patch_artifact_id:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if patch_artifact.workspace_id != cloud_run.workspace_id:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if patch_artifact.task_id != cloud_run.task_id:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    return patch_artifact.diff_text


def _validate_local_inline_scope(
    session: Session,
    *,
    cloud_run: CloudRun,
    source: ArtifactSource,
) -> None:
    if source.storage_ref is None:
        return
    if not _is_local_inline_uri(source.storage_ref.uri):
        return
    target_object_id = _local_inline_object_id(source.storage_ref.uri)
    if target_object_id is None:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if (
        source.stored_object_id is not None
        and target_object_id != source.stored_object_id
    ):
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")

    stored_object = session.get(CloudRunStoredObject, target_object_id)
    if stored_object is None:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if (
        stored_object.cloud_run_id != cloud_run.id
        or stored_object.workspace_id != cloud_run.workspace_id
        or stored_object.kind != source.descriptor.kind
    ):
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    _raise_if_expired(stored_object.expires_at)
    if stored_object.content_type != source.storage_ref.content_type:
        raise HTTPException(
            status_code=400,
            detail="Object storage reference content type mismatch",
        )


def _local_inline_stored_object(
    session: Session,
    uri: str,
) -> CloudRunStoredObject | None:
    stored_object_id = _local_inline_object_id(uri)
    if stored_object_id is None:
        return None
    return session.get(CloudRunStoredObject, stored_object_id)


def _is_local_inline_uri(uri: str) -> bool:
    return urlsplit(uri).scheme == "local-inline"


def _local_inline_object_id(uri: str) -> str | None:
    parsed = urlsplit(uri)
    if parsed.scheme != "local-inline":
        return None
    if parsed.netloc != "cloud-run-objects":
        return None
    stored_object_id = parsed.path.lstrip("/")
    return stored_object_id or None


def _raise_if_expired(expires_at: datetime | None) -> None:
    if expires_at is None:
        return
    if _as_utc(expires_at) <= utc_now():
        raise HTTPException(status_code=410, detail="Cloud run artifact expired")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _artifact_kind_or_none(kind: str) -> ArtifactKind | None:
    if kind not in ARTIFACT_LABELS:
        return None
    return cast(ArtifactKind, kind)


def _stable_artifact_id(kind: ArtifactKind, uri: str, sha256_value: str) -> str:
    key = f"{kind}\0{uri}\0{sha256_value}"
    return f"{kind}_{sha256(key.encode('utf-8')).hexdigest()[:16]}"


def _provider_name_from_uri(uri: str) -> str:
    scheme = urlsplit(uri).scheme
    if scheme == "local-inline":
        return "local_inline"
    if scheme == "oss":
        return "aliyun_oss"
    if scheme == "patch-artifact":
        return "patch_artifact"
    return "unknown"


def _redact_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    netloc = _redacted_netloc(parsed)
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _redacted_netloc(parsed: SplitResult) -> str:
    if parsed.hostname is None:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        return host
    return f"{host}:{port}"
