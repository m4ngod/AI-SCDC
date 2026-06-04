from __future__ import annotations

from datetime import timezone
import os

from fastapi import HTTPException, status
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, SandboxProfile, Task, utc_now
from ai_company_api.schemas.api import (
    RemoteWorkerCommandPayload,
    RemoteWorkerPayloadRead,
    RemoteWorkerPayloadRequest,
)
from ai_company_api.services.cloud_runner import verify_cloud_run_callback_token_or_403
from ai_company_api.services.github_repository import (
    get_active_github_credential,
    validate_github_repository_url,
)
from ai_company_api.services.repository import get_repository
from ai_company_api.services.sandbox_profiles import validate_sandbox_profile_for_repo
from ai_company_api.services.secret_vault import DevSecretVault


def get_remote_worker_payload(
    session: Session,
    *,
    lease_id: str,
    data: RemoteWorkerPayloadRequest,
) -> RemoteWorkerPayloadRead:
    cloud_run = _get_current_worker_cloud_run_or_409(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
    )
    verify_cloud_run_callback_token_or_403(
        cloud_run,
        worker_id=data.worker_id,
        callback_token=data.callback_token,
    )
    task = session.get(Task, cloud_run.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    repository = get_repository(session, cloud_run.repo_id)
    if repository.github_credential_id is None:
        raise HTTPException(status_code=404, detail="GitHub credential not found")
    validate_github_repository_url(
        repository.repo_url,
        owner=repository.github_owner or "",
        repo=repository.github_repo or "",
    )
    if cloud_run.sandbox_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="Remote worker payload requires a sandbox profile",
        )
    profile = validate_sandbox_profile_for_repo(
        session,
        cloud_run.sandbox_profile_id,
        project_id=task.project_id,
        repo_id=repository.id,
    )
    patch_command, test_commands = _select_profile_commands_for_cloud_run(
        profile,
        patch_command_key=cloud_run.patch_command_key,
        test_command_keys=cloud_run.test_command_keys or [],
    )
    credential = get_active_github_credential(session, repository.github_credential_id)
    clone_token = DevSecretVault().open(credential.encrypted_token)
    return RemoteWorkerPayloadRead(
        cloud_run_id=cloud_run.id,
        task_id=task.id,
        title=task.title,
        description=task.description,
        repo_url=repository.repo_url,
        github_owner=repository.github_owner,
        github_repo=repository.github_repo,
        base_branch=cloud_run.base_branch or repository.default_branch,
        head_branch=cloud_run.head_branch,
        allowed_paths=task.allowed_paths or [],
        required_tests=task.required_tests or [],
        patch_command=patch_command,
        test_commands=test_commands,
        env=_sandbox_profile_env(profile.allowed_env_vars or []),
        network_enabled=profile.network_enabled,
        clone_token=clone_token,
    )


def _get_current_worker_cloud_run_or_409(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
) -> CloudRun:
    cloud_run = session.exec(
        select(CloudRun).where(CloudRun.lease_id == lease_id)
    ).first()
    if cloud_run is None or cloud_run.worker_id != worker_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    if cloud_run.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    if cloud_run.completed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    lease_expires_at = cloud_run.lease_expires_at
    if lease_expires_at is not None and lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    if lease_expires_at is None or lease_expires_at < utc_now():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    return cloud_run


def _select_profile_commands_for_cloud_run(
    profile: SandboxProfile,
    *,
    patch_command_key: str | None,
    test_command_keys: list[str],
) -> tuple[RemoteWorkerCommandPayload, list[RemoteWorkerCommandPayload]]:
    patch_command = _select_command(
        profile.patch_commands or [],
        patch_command_key,
        kind="patch",
    )
    if test_command_keys:
        test_commands = [
            _select_command(profile.test_commands or [], key, kind="test")
            for key in test_command_keys
        ]
    else:
        test_commands = [
            _command_payload(command)
            for command in (profile.test_commands or [])
            if command.get("is_default") is True
        ]
        if profile.test_commands and len(test_commands) != 1:
            raise HTTPException(
                status_code=400,
                detail="Sandbox profile requires exactly one default test command",
            )
    return patch_command, test_commands


def _select_command(
    commands: list[dict],
    requested_key: str | None,
    *,
    kind: str,
) -> RemoteWorkerCommandPayload:
    if requested_key is not None:
        for command in commands:
            if command.get("key") == requested_key:
                return _command_payload(command)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sandbox {kind} command key",
        )
    defaults = [command for command in commands if command.get("is_default") is True]
    if len(defaults) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Sandbox profile requires exactly one default {kind} command",
        )
    return _command_payload(defaults[0])


def _command_payload(command: dict) -> RemoteWorkerCommandPayload:
    return RemoteWorkerCommandPayload(
        key=command["key"],
        label=command["label"],
        command=command["command"],
        timeout_seconds=command.get("timeout_seconds", 300),
    )


def _sandbox_profile_env(allowed_env_vars: list[str]) -> dict[str, str]:
    return {name: os.environ[name] for name in allowed_env_vars if name in os.environ}
