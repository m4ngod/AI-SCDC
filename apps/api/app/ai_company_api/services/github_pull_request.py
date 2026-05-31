from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import tempfile
from urllib import request

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    CloudRun,
    PatchApproval,
    PatchArtifact,
    PullRequestRecord,
    Repository,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    PatchApprovalRead,
    PatchArtifactRead,
    PullRequestRead,
    PullRequestResultRead,
    TaskRead,
)
from ai_company_api.services.github_repository import get_active_github_credential
from ai_company_api.services.repository import create_task_event, get_task
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


@dataclass(frozen=True)
class CreatedPullRequest:
    number: int
    url: str


class GitHubPullRequestAdapter:
    def create_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        repo_url: str,
        token: str,
        base_branch: str,
        head_branch: str,
        diff_text: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        try:
            with tempfile.TemporaryDirectory(prefix="ai-scdc-pr-") as temp_dir:
                worktree = Path(temp_dir) / repo
                askpass_path = _write_git_askpass(Path(temp_dir))
                git_env = _git_auth_env(askpass_path, token)
                _run_git(["clone", repo_url, str(worktree)], token, env=git_env)
                _run_git(
                    ["checkout", "-B", head_branch, f"origin/{base_branch}"],
                    token,
                    cwd=worktree,
                    env=git_env,
                )
                patch_path = worktree / "ai-scdc.patch"
                patch_path.write_text(diff_text, encoding="utf-8")
                _run_git(["apply", str(patch_path)], token, cwd=worktree, env=git_env)
                patch_path.unlink(missing_ok=True)
                _run_git(["add", "-A"], token, cwd=worktree, env=git_env)
                _run_git(
                    [
                        "-c",
                        "user.email=ai-scdc@example.local",
                        "-c",
                        "user.name=AI SCDC",
                        "commit",
                        "-m",
                        title,
                    ],
                    token,
                    cwd=worktree,
                    env=git_env,
                )
                _run_git(
                    ["push", "origin", f"HEAD:{head_branch}"],
                    token,
                    cwd=worktree,
                    env=git_env,
                )

            payload = json.dumps(
                {
                    "title": title,
                    "head": head_branch,
                    "base": base_branch,
                    "body": body,
                }
            ).encode("utf-8")
            api_request = request.Request(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                data=payload,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "ai-scdc-dev",
                },
                method="POST",
            )
            with request.urlopen(api_request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            return CreatedPullRequest(
                number=int(data["number"]),
                url=str(data["html_url"]),
            )
        except Exception as exc:
            message = _redact_token(str(exc), token)
            raise RuntimeError(f"GitHub pull request creation failed: {message}") from exc


class FakeGitHubPullRequestAdapter:
    def create_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        repo_url: str,
        token: str,
        base_branch: str,
        head_branch: str,
        diff_text: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        return CreatedPullRequest(
            number=1,
            url=f"https://github.com/{owner}/{repo}/pull/1",
        )


GITHUB_PR_ADAPTER_ENV = "AI_SCDC_GITHUB_PR_ADAPTER"
DEFAULT_ADAPTER = FakeGitHubPullRequestAdapter()


def _default_pull_request_adapter() -> GitHubPullRequestAdapter | FakeGitHubPullRequestAdapter:
    if os.getenv(GITHUB_PR_ADAPTER_ENV, "").strip().lower() == "real":
        return GitHubPullRequestAdapter()
    return DEFAULT_ADAPTER


def create_pull_request_for_approval(
    session: Session,
    approval_id: str,
    *,
    adapter: GitHubPullRequestAdapter | FakeGitHubPullRequestAdapter | None = None,
    vault: SecretVault | None = None,
) -> tuple[PullRequestResultRead, int]:
    approval = session.get(PatchApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Patch approval not found")

    existing = _existing_pull_request(session, approval.id)
    if existing is not None:
        return _handle_existing_pull_request(session, existing)

    artifact = _get_patch_artifact_entity(session, approval.patch_artifact_id)
    task = get_task(session, approval.task_id)
    current_status = TaskStatus(task.status)
    if current_status != TaskStatus.HUMAN_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be HUMAN_APPROVAL before pull request creation",
                "current_status": current_status.value,
                "expected_status": TaskStatus.HUMAN_APPROVAL.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        )

    cloud_run = _latest_cloud_run_for_artifact(session, artifact)
    if cloud_run is None:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Pull request creation requires a linked cloud run",
                "patch_artifact_id": artifact.id,
            },
        )

    repository = _get_github_repository(session, cloud_run.repo_id)
    credential_id = repository.github_credential_id
    if credential_id is None:
        raise HTTPException(status_code=400, detail="GitHub credential is required")

    credential = get_active_github_credential(session, credential_id)
    token = (vault or DevSecretVault()).open(credential.encrypted_token)
    base_branch = cloud_run.base_branch or repository.default_branch
    record = PullRequestRecord(
        workspace_id=approval.workspace_id,
        project_id=approval.project_id,
        task_id=approval.task_id,
        repo_id=repository.id,
        patch_artifact_id=artifact.id,
        patch_approval_id=approval.id,
        cloud_run_id=cloud_run.id,
        head_branch=cloud_run.head_branch,
        base_branch=base_branch,
        github_pr_number=0,
        github_pr_url="",
        status="creating",
        created_by="dev_user",
    )
    session.add(record)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = _existing_pull_request(session, approval.id)
        if existing is not None:
            return _handle_existing_pull_request(session, existing)
        raise HTTPException(
            status_code=409,
            detail="Pull request record could not be created because of a uniqueness conflict",
        ) from exc

    record_id = record.id
    try:
        created_pr = (adapter or _default_pull_request_adapter()).create_pull_request(
            owner=repository.github_owner or "",
            repo=repository.github_repo or "",
            repo_url=repository.repo_url,
            token=token,
            base_branch=base_branch,
            head_branch=cloud_run.head_branch,
            diff_text=artifact.diff_text,
            title=f"{task.title}",
            body=f"Created from patch artifact {artifact.id}.",
        )
    except Exception as exc:
        _mark_pull_request_failed(session, record_id)
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Pull request creation failed",
                "error": _redact_token(str(exc), token),
            },
        ) from exc

    _persist_pull_request_finalizing(session, record_id, created_pr)
    return _finalize_pull_request(session, record_id, 201)


def list_pull_requests_for_patch_artifact(
    session: Session,
    patch_artifact_id: str,
) -> list[PullRequestRead]:
    _get_patch_artifact_entity(session, patch_artifact_id)
    statement = (
        select(PullRequestRecord)
        .where(PullRequestRecord.patch_artifact_id == patch_artifact_id)
        .order_by(PullRequestRecord.created_at, PullRequestRecord.id)
    )
    return [_pull_request_read(record) for record in session.exec(statement).all()]


def get_pull_request(session: Session, pull_request_id: str) -> PullRequestRead:
    record = session.get(PullRequestRecord, pull_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    return _pull_request_read(record)


def _existing_pull_request(
    session: Session,
    approval_id: str,
) -> PullRequestRecord | None:
    statement = (
        select(PullRequestRecord)
        .where(PullRequestRecord.patch_approval_id == approval_id)
        .order_by(PullRequestRecord.created_at, PullRequestRecord.id)
        .limit(1)
    )
    return session.exec(statement).first()


def _handle_existing_pull_request(
    session: Session,
    record: PullRequestRecord,
) -> tuple[PullRequestResultRead, int]:
    if record.status == "created":
        return _pull_request_result_read(session, record), 200
    if record.status == "creating":
        raise HTTPException(
            status_code=409,
            detail="Pull request is already being created for this patch approval",
        )
    if record.status == "failed":
        raise HTTPException(
            status_code=409,
            detail="Pull request creation previously failed for this patch approval",
        )
    if record.status == "finalizing":
        if record.github_pr_number > 0 and record.github_pr_url:
            return _finalize_pull_request(session, record.id, 200)
        raise HTTPException(
            status_code=409,
            detail="Pull request finalization is missing GitHub metadata",
        )
    raise HTTPException(
        status_code=409,
        detail=f"Pull request is in unsupported status {record.status}",
    )


def _persist_pull_request_finalizing(
    session: Session,
    record_id: str,
    created_pr: CreatedPullRequest,
) -> None:
    record = session.get(PullRequestRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    record.github_pr_number = created_pr.number
    record.github_pr_url = created_pr.url
    record.status = "finalizing"
    session.add(record)
    session.commit()


def _finalize_pull_request(
    session: Session,
    record_id: str,
    status_code: int,
) -> tuple[PullRequestResultRead, int]:
    record = session.get(PullRequestRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Pull request not found")

    task = get_task(session, record.task_id)
    if TaskStatus(task.status) != TaskStatus.PR_CREATED:
        _transition_task_to_pr_created(session, task)
        create_task_event(
            session,
            task.id,
            "pull_request_created",
            "system",
            "github_pull_request",
            {
                "pull_request_id": record.id,
                "patch_approval_id": record.patch_approval_id,
                "patch_artifact_id": record.patch_artifact_id,
                "github_pr_number": record.github_pr_number,
                "github_pr_url": record.github_pr_url,
            },
        )

    record.status = "created"
    session.add(record)
    session.commit()
    session.refresh(record)
    return _pull_request_result_read(session, record), status_code


def _latest_cloud_run_for_artifact(
    session: Session,
    artifact: PatchArtifact,
) -> CloudRun | None:
    statement = (
        select(CloudRun)
        .where(CloudRun.patch_artifact_id == artifact.id)
        .order_by(CloudRun.created_at.desc(), CloudRun.id.desc())
        .limit(1)
    )
    cloud_run = session.exec(statement).first()
    if cloud_run is not None:
        return cloud_run

    fallback_statement = (
        select(CloudRun)
        .where(
            CloudRun.task_id == artifact.task_id,
            CloudRun.local_run_id == artifact.local_run_id,
        )
        .order_by(CloudRun.created_at.desc(), CloudRun.id.desc())
        .limit(1)
    )
    return session.exec(fallback_statement).first()


def _get_github_repository(session: Session, repo_id: str) -> Repository:
    repository = session.get(Repository, repo_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if repository.provider != "github":
        raise HTTPException(status_code=400, detail="Pull requests require a GitHub repository")
    if repository.connection_status != "active":
        raise HTTPException(status_code=400, detail="GitHub repository is not active")
    if not repository.github_owner or not repository.github_repo:
        raise HTTPException(status_code=400, detail="GitHub repository metadata is incomplete")
    return repository


def _get_patch_artifact_entity(
    session: Session,
    patch_artifact_id: str,
) -> PatchArtifact:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return artifact


def _transition_task_to_pr_created(session: Session, task: Task) -> None:
    current_status = TaskStatus(task.status)
    try:
        next_status = validate_transition(
            current_status,
            TaskStatus.PR_CREATED,
            actor_type="system",
        )
    except InvalidTaskTransition as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "current_status": current_status.value,
                "requested_status": TaskStatus.PR_CREATED.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        ) from exc

    task.status = next_status
    task.updated_at = utc_now()
    session.add(task)
    create_task_event(
        session,
        task.id,
        "task_transitioned",
        "system",
        "github_pull_request",
        {"from_status": current_status.value, "to_status": next_status.value},
    )


def _pull_request_result_read(
    session: Session,
    record: PullRequestRecord,
) -> PullRequestResultRead:
    task = get_task(session, record.task_id)
    artifact = _get_patch_artifact_entity(session, record.patch_artifact_id)
    approval = session.get(PatchApproval, record.patch_approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Patch approval not found")
    return PullRequestResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        approval=_approval_read(approval),
        pull_request=_pull_request_read(record),
    )


def _task_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        conversation_id=task.conversation_id,
        parent_task_id=task.parent_task_id,
        title=task.title,
        description=task.description,
        role_required=task.role_required,
        status=TaskStatus(task.status),
        priority=task.priority,
        risk_level=task.risk_level,
        acceptance_criteria=task.acceptance_criteria,
        allowed_paths=task.allowed_paths,
        required_tests=task.required_tests,
        assigned_agent_profile_id=task.assigned_agent_profile_id,
        repo_id=task.repo_id,
        branch_name=task.branch_name,
        worktree_ref=task.worktree_ref,
        budget_limit=task.budget_limit,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _patch_artifact_read(artifact: PatchArtifact) -> PatchArtifactRead:
    return PatchArtifactRead(
        id=artifact.id,
        workspace_id=artifact.workspace_id,
        project_id=artifact.project_id,
        task_id=artifact.task_id,
        local_run_id=artifact.local_run_id,
        summary=artifact.summary,
        files_changed=artifact.files_changed,
        tests_run=artifact.tests_run,
        test_result=artifact.test_result,
        risks=artifact.risks,
        diff_text=artifact.diff_text,
        created_at=artifact.created_at,
    )


def _approval_read(approval: PatchApproval) -> PatchApprovalRead:
    return PatchApprovalRead(
        id=approval.id,
        workspace_id=approval.workspace_id,
        project_id=approval.project_id,
        task_id=approval.task_id,
        local_run_id=approval.local_run_id,
        patch_artifact_id=approval.patch_artifact_id,
        review_id=approval.review_id,
        status=approval.status,
        approved_by=approval.approved_by,
        merge_instructions=approval.merge_instructions,
        created_at=approval.created_at,
    )


def _pull_request_read(record: PullRequestRecord) -> PullRequestRead:
    return PullRequestRead(
        id=record.id,
        workspace_id=record.workspace_id,
        project_id=record.project_id,
        task_id=record.task_id,
        repo_id=record.repo_id,
        patch_artifact_id=record.patch_artifact_id,
        patch_approval_id=record.patch_approval_id,
        cloud_run_id=record.cloud_run_id,
        head_branch=record.head_branch,
        base_branch=record.base_branch,
        github_pr_number=record.github_pr_number,
        github_pr_url=record.github_pr_url,
        status=record.status,
        created_by=record.created_by,
        created_at=record.created_at,
    )


def _run_git(
    args: list[str],
    token: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        output = _redact_token(result.stderr or result.stdout, token)
        raise RuntimeError(output)


def _redact_token(message: str, token: str) -> str:
    if token:
        message = message.replace(token, "[redacted]")
    return message


def _write_git_askpass(directory: Path) -> Path:
    if os.name == "nt":
        askpass_path = directory / "git-askpass.bat"
        askpass_path.write_text(
            "@echo off\r\n"
            "echo %1 | findstr /I \"Username\" >nul\r\n"
            "if %ERRORLEVEL%==0 (\r\n"
            "  echo x-access-token\r\n"
            ") else (\r\n"
            "  echo %AI_SCDC_GIT_TOKEN%\r\n"
            ")\r\n",
            encoding="utf-8",
        )
    else:
        askpass_path = directory / "git-askpass.sh"
        askpass_path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$AI_SCDC_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass_path.chmod(0o700)
    return askpass_path


def _git_auth_env(askpass_path: Path, token: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AI_SCDC_GIT_TOKEN": token,
            "GIT_ASKPASS": str(askpass_path),
            "GIT_CONFIG_PARAMETERS": _git_config_parameters_without_credential_helper(
                env.get("GIT_CONFIG_PARAMETERS")
            ),
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def _git_config_parameters_without_credential_helper(existing: str | None) -> str:
    disable_credential_helper = "'credential.helper='"
    existing_parameters = (existing or "").strip()
    if not existing_parameters:
        return disable_credential_helper
    if disable_credential_helper in existing_parameters:
        return existing_parameters
    return f"{existing_parameters} {disable_credential_helper}"


def _mark_pull_request_failed(session: Session, record_id: str) -> None:
    record = session.get(PullRequestRecord, record_id)
    if record is None:
        return
    record.status = "failed"
    session.add(record)
    session.commit()
