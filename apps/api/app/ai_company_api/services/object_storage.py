from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlparse

from sqlmodel import Session

from ai_company_api.models.entities import CloudRunStoredObject
from ai_company_api.services.aliyun_clients import (
    AliyunOssPutObjectRequest,
    get_aliyun_client_bundle,
)
from ai_company_api.services.aliyun_config import AliyunSettings, require_aliyun_settings


ARTIFACT_KINDS = {"diff", "log", "command_result", "test_result", "manifest"}
LOCAL_INLINE_SCHEME = "local-inline"
LOCAL_INLINE_AUTHORITY = "cloud-run-objects"
ALIYUN_OSS_SCHEME = "oss"


class ObjectStorageError(Exception):
    pass


class ObjectStorageReadError(ObjectStorageError):
    pass


class ObjectStorageProviderNotFound(ObjectStorageError):
    pass


@dataclass
class ObjectStorageWrite:
    workspace_id: str
    cloud_run_id: str
    kind: str
    content: str
    content_type: str = "text/plain"


@dataclass
class ObjectStorageRef:
    kind: str
    uri: str
    sha256: str
    size_bytes: int
    content_type: str = "text/plain"


class ObjectStorageProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        ...

    def put_text(
        self,
        session: Session,
        write: ObjectStorageWrite,
    ) -> ObjectStorageRef:
        ...

    def read_text(
        self,
        session: Session,
        ref: ObjectStorageRef,
    ) -> str:
        ...


class LocalInlineObjectStorageProvider:
    name = "local_inline"

    def validate_configuration(self) -> None:
        return None

    def put_text(
        self,
        session: Session,
        write: ObjectStorageWrite,
    ) -> ObjectStorageRef:
        _validate_artifact_kind(write.kind)
        content_bytes = write.content.encode("utf-8")
        stored_object = CloudRunStoredObject(
            workspace_id=write.workspace_id,
            cloud_run_id=write.cloud_run_id,
            kind=write.kind,
            uri="",
            sha256=sha256(content_bytes).hexdigest(),
            size_bytes=len(content_bytes),
            content_type=write.content_type,
            text_content=write.content,
        )
        session.add(stored_object)
        session.flush()
        stored_object.uri = _local_inline_uri(stored_object.id)
        session.add(stored_object)
        session.flush()
        return ObjectStorageRef(
            kind=stored_object.kind,
            uri=stored_object.uri,
            sha256=stored_object.sha256,
            size_bytes=stored_object.size_bytes,
            content_type=stored_object.content_type,
        )

    def read_text(
        self,
        session: Session,
        ref: ObjectStorageRef,
    ) -> str:
        _validate_artifact_kind(ref.kind)
        stored_object_id = _parse_local_inline_object_id(ref.uri)
        stored_object = session.get(CloudRunStoredObject, stored_object_id)
        if stored_object is None:
            raise ObjectStorageReadError("Object storage reference was not found")
        if stored_object.kind != ref.kind:
            raise ObjectStorageReadError("Object storage reference kind mismatch")
        if stored_object.sha256 != ref.sha256:
            raise ObjectStorageReadError("Object storage reference sha256 mismatch")
        if stored_object.size_bytes != ref.size_bytes:
            raise ObjectStorageReadError("Object storage reference size mismatch")

        content_bytes = stored_object.text_content.encode("utf-8")
        if sha256(content_bytes).hexdigest() != ref.sha256:
            raise ObjectStorageReadError("Object storage content sha256 mismatch")
        if len(content_bytes) != ref.size_bytes:
            raise ObjectStorageReadError("Object storage content size mismatch")
        return stored_object.text_content


class AliyunOssObjectStorageProvider:
    name = "aliyun_oss"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )

    def put_text(
        self,
        session: Session,
        write: ObjectStorageWrite,
    ) -> ObjectStorageRef:
        self.validate_configuration()
        _validate_artifact_kind(write.kind)
        settings = _require_aliyun_oss_settings(self.name)
        content_bytes = write.content.encode("utf-8")
        digest = sha256(content_bytes).hexdigest()
        suffix = "json" if write.content_type == "application/json" else "txt"
        oss_prefix = _normalized_oss_prefix(settings.oss_prefix)
        object_key = (
            f"{oss_prefix}workspaces/{write.workspace_id}/"
            f"cloud-runs/{write.cloud_run_id}/{write.kind}/{digest}.{suffix}"
        )
        get_aliyun_client_bundle(settings).oss.put_object(
            AliyunOssPutObjectRequest(
                bucket=settings.oss_bucket or "",
                object_key=object_key,
                content=content_bytes,
                content_type=write.content_type,
            )
        )
        return ObjectStorageRef(
            kind=write.kind,
            uri=f"{ALIYUN_OSS_SCHEME}://{settings.oss_bucket}/{object_key}",
            sha256=digest,
            size_bytes=len(content_bytes),
            content_type=write.content_type,
        )

    def read_text(
        self,
        session: Session,
        ref: ObjectStorageRef,
    ) -> str:
        self.validate_configuration()
        _validate_artifact_kind(ref.kind)
        settings = _require_aliyun_oss_settings(self.name)
        bucket, object_key = _parse_oss_ref(ref.uri)
        if bucket != settings.oss_bucket:
            raise ObjectStorageReadError("Object storage reference bucket mismatch")
        expected_prefix = f"{_normalized_oss_prefix(settings.oss_prefix)}workspaces/"
        if not object_key.startswith(expected_prefix):
            raise ObjectStorageReadError("Object storage reference prefix mismatch")
        if f"/{ref.kind}/" not in f"/{object_key}":
            raise ObjectStorageReadError("Object storage reference kind mismatch")
        content = get_aliyun_client_bundle(settings).oss.get_object_text(
            bucket,
            object_key,
        )
        content_bytes = content.encode("utf-8")
        if sha256(content_bytes).hexdigest() != ref.sha256:
            raise ObjectStorageReadError("Object storage content sha256 mismatch")
        if len(content_bytes) != ref.size_bytes:
            raise ObjectStorageReadError("Object storage content size mismatch")
        return content


def get_object_storage_provider(name: str | None) -> ObjectStorageProvider:
    if name in (None, "local_inline"):
        return LocalInlineObjectStorageProvider()
    if name == "aliyun_oss":
        return AliyunOssObjectStorageProvider()
    raise ObjectStorageProviderNotFound(f"Unknown object storage provider: {name}")


def _validate_artifact_kind(kind: str) -> None:
    if kind not in ARTIFACT_KINDS:
        raise ObjectStorageReadError(f"Unsupported object storage artifact kind: {kind}")


def _local_inline_uri(stored_object_id: str) -> str:
    return f"{LOCAL_INLINE_SCHEME}://{LOCAL_INLINE_AUTHORITY}/{stored_object_id}"


def _parse_local_inline_object_id(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != LOCAL_INLINE_SCHEME:
        raise ObjectStorageReadError("Object storage reference scheme mismatch")
    if parsed.netloc != LOCAL_INLINE_AUTHORITY:
        raise ObjectStorageReadError("Object storage reference authority mismatch")
    stored_object_id = parsed.path.lstrip("/")
    if not stored_object_id:
        raise ObjectStorageReadError("Object storage reference object id missing")
    return stored_object_id


def _parse_oss_ref(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != ALIYUN_OSS_SCHEME:
        raise ObjectStorageReadError("Object storage reference scheme mismatch")
    if not parsed.netloc:
        raise ObjectStorageReadError("Object storage reference bucket missing")
    if parsed.query or parsed.fragment:
        raise ObjectStorageReadError(
            "Object storage reference must not include query or fragment"
        )
    object_key = parsed.path.lstrip("/")
    if not object_key:
        raise ObjectStorageReadError("Object storage reference object key missing")
    return parsed.netloc, object_key


def _require_aliyun_oss_settings(provider_name: str) -> AliyunSettings:
    return require_aliyun_settings(
        provider_name=provider_name,
        required_names=(
            "region_id",
            "access_key_id",
            "access_key_secret",
            "oss_endpoint",
            "oss_bucket",
        ),
    )


def _normalized_oss_prefix(prefix: str) -> str:
    stripped = prefix.strip("/")
    if not stripped:
        return ""
    return f"{stripped}/"
