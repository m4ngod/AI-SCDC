from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    GitHubCredential,
    GitHubCredentialStatus,
    Repository,
    utc_now,
)
from ai_company_api.schemas.api import (
    GitHubCredentialCreate,
    GitHubCredentialRead,
    GitHubRepositoryCreate,
    RepositoryRead,
)
from ai_company_api.services.repository import _repository_read, get_project
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _github_credential_read(credential: GitHubCredential) -> GitHubCredentialRead:
    return GitHubCredentialRead(
        id=credential.id,
        workspace_id=credential.workspace_id,
        display_name=credential.display_name,
        token_last4=credential.token_last4,
        status=_enum_value(credential.status),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def list_github_credentials(session: Session) -> list[GitHubCredentialRead]:
    statement = select(GitHubCredential).order_by(
        GitHubCredential.created_at,
        GitHubCredential.id,
    )
    return [_github_credential_read(credential) for credential in session.exec(statement)]


def create_github_credential(
    session: Session,
    data: GitHubCredentialCreate,
    vault: SecretVault | None = None,
) -> GitHubCredentialRead:
    sealed = (vault or DevSecretVault()).seal(data.token.get_secret_value())
    credential = GitHubCredential(
        display_name=data.display_name,
        token_last4=sealed.secret_last4,
        encrypted_token=sealed.encrypted_secret,
    )
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _github_credential_read(credential)


def get_active_github_credential(
    session: Session,
    credential_id: str,
) -> GitHubCredential:
    credential = session.get(GitHubCredential, credential_id)
    if (
        credential is None
        or _enum_value(credential.status) != GitHubCredentialStatus.ACTIVE.value
    ):
        raise HTTPException(status_code=404, detail="GitHub credential not found")
    return credential


def delete_github_credential(
    session: Session,
    credential_id: str,
) -> GitHubCredentialRead:
    credential = session.get(GitHubCredential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="GitHub credential not found")

    credential.status = GitHubCredentialStatus.DELETED
    credential.updated_at = utc_now()
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _github_credential_read(credential)


def create_github_repository(
    session: Session,
    project_id: str,
    data: GitHubRepositoryCreate,
) -> RepositoryRead:
    get_project(session, project_id)
    get_active_github_credential(session, data.github_credential_id)
    repo_url = validate_github_repository_url(
        data.repo_url,
        owner=data.github_owner,
        repo=data.github_repo,
    )

    repository = Repository(
        project_id=project_id,
        name=data.name,
        local_path="",
        default_branch=data.default_branch,
        provider="github",
        repo_url=repo_url,
        github_owner=data.github_owner,
        github_repo=data.github_repo,
        github_credential_id=data.github_credential_id,
        connection_status="active",
    )
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return _repository_read(repository)


def validate_github_repository_url(repo_url: str, *, owner: str, repo: str) -> str:
    try:
        parsed = urlsplit(repo_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="GitHub repository URL must match owner/repo",
        ) from exc

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail="GitHub repository URL must not include credentials",
        )
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise HTTPException(
            status_code=400,
            detail="GitHub repository URL must match owner/repo",
        )

    expected_paths = {
        f"/{owner}/{repo}",
        f"/{owner}/{repo}.git",
    }
    if parsed.path not in expected_paths or parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=400,
            detail="GitHub repository URL must match owner/repo",
        )
    return repo_url
