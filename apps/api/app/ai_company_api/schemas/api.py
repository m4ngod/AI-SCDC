from typing import Any

from pydantic import BaseModel, Field

from ai_company_api.services.task_state import TaskStatus


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    conversation_type: str = "planning"


class MessageCreate(BaseModel):
    sender_type: str
    content: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    role_required: str
    conversation_id: str | None = None
    risk_level: str = "medium"
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    status: TaskStatus


class DevIdentity(BaseModel):
    user_id: str
    workspace_id: str
