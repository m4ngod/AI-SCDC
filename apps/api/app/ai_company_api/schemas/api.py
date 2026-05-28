from typing import Any

from pydantic import BaseModel, Field

from ai_company_api.services.task_state import TaskStatus


class ProjectCreate(BaseModel):
    name: str


class ConversationCreate(BaseModel):
    title: str = "New conversation"


class MessageCreate(BaseModel):
    role: str
    content: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    acceptance_criteria: list[dict[str, Any]] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    status: TaskStatus


class DevIdentity(BaseModel):
    user_id: str
    workspace_id: str
