from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import SandboxProfile
from ai_company_api.schemas.api import (
    SandboxCommand,
    SandboxProfileCreate,
    SandboxProfileRead,
)
from ai_company_api.services.docker_sandbox import (
    is_safe_sandbox_env_name,
    validate_docker_image,
)
from ai_company_api.services.repository import get_project, get_repository


def create_sandbox_profile(
    session: Session,
    project_id: str,
    data: SandboxProfileCreate,
) -> SandboxProfileRead:
    project = get_project(session, project_id)
    repository = get_repository(session, data.repo_id)
    if repository.project_id != project.id:
        raise HTTPException(status_code=400, detail="Repository does not belong to project")
    if repository.provider != "github":
        raise HTTPException(
            status_code=400,
            detail="Sandbox profiles require a GitHub repository",
        )

    _validate_unique_command_keys([*data.patch_commands, *data.test_commands])
    _validate_sandbox_commands(data.patch_commands, command_kind="patch")
    _validate_sandbox_commands(data.test_commands, command_kind="test")
    docker_image = validate_docker_image(data.docker_image)
    if docker_image is None:
        raise HTTPException(status_code=400, detail="Invalid Docker image")
    allowed_env_vars = _validated_allowed_env_vars(data.allowed_env_vars)

    profile = SandboxProfile(
        workspace_id=project.workspace_id,
        project_id=project.id,
        repo_id=repository.id,
        name=data.name,
        docker_image=docker_image,
        patch_commands=[command.model_dump() for command in data.patch_commands],
        test_commands=[command.model_dump() for command in data.test_commands],
        allowed_env_vars=allowed_env_vars,
        network_enabled=data.network_enabled,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _sandbox_profile_read(profile)


def list_sandbox_profiles(session: Session, project_id: str) -> list[SandboxProfileRead]:
    get_project(session, project_id)
    statement = (
        select(SandboxProfile)
        .where(SandboxProfile.project_id == project_id)
        .order_by(SandboxProfile.created_at, SandboxProfile.id)
    )
    return [_sandbox_profile_read(profile) for profile in session.exec(statement).all()]


def get_sandbox_profile_read(
    session: Session,
    sandbox_profile_id: str,
) -> SandboxProfileRead:
    return _sandbox_profile_read(get_sandbox_profile(session, sandbox_profile_id))


def get_sandbox_profile(session: Session, profile_id: str) -> SandboxProfile:
    profile = session.get(SandboxProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Sandbox profile not found")
    return profile


def validate_sandbox_profile_for_repo(
    session: Session,
    profile_id: str,
    *,
    project_id: str,
    repo_id: str,
) -> SandboxProfile:
    profile = get_sandbox_profile(session, profile_id)
    if profile.status != "active":
        raise HTTPException(status_code=400, detail="Sandbox profile is not active")
    if profile.project_id != project_id:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile does not belong to project",
        )
    if profile.repo_id != repo_id:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile does not belong to repository",
        )
    return profile


def _validate_sandbox_commands(
    commands: list[SandboxCommand],
    *,
    command_kind: str,
) -> None:
    if command_kind == "test" and not commands:
        return

    default_count = sum(1 for command in commands if command.is_default)
    if command_kind == "patch" and default_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile requires exactly one default patch command",
        )
    if command_kind == "test" and default_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile requires exactly one default test command",
        )


def _validate_unique_command_keys(commands: list[SandboxCommand]) -> None:
    command_keys = [command.key for command in commands]
    if len(command_keys) != len(set(command_keys)):
        raise HTTPException(
            status_code=400,
            detail="Sandbox command keys must be unique",
        )


def _validated_allowed_env_vars(names: list[str]) -> list[str]:
    deduped = list(dict.fromkeys(names))
    if any(not is_safe_sandbox_env_name(name) for name in deduped):
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile allowed env vars are invalid",
        )
    return deduped


def _sandbox_profile_read(profile: SandboxProfile) -> SandboxProfileRead:
    return SandboxProfileRead(
        id=profile.id,
        workspace_id=profile.workspace_id,
        project_id=profile.project_id,
        repo_id=profile.repo_id,
        name=profile.name,
        docker_image=profile.docker_image,
        patch_commands=profile.patch_commands or [],
        test_commands=profile.test_commands or [],
        allowed_env_vars=profile.allowed_env_vars or [],
        network_enabled=profile.network_enabled,
        status=profile.status,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
