"""Pydantic request/response models.

Anything crossing the API boundary is declared here so validation happens
before a request reaches business logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# --- Auth -----------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    display_name: str = Field(default="", max_length=80)

    @field_validator("display_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return value.strip()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


# --- Workspaces -----------------------------------------------------------
class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Title cannot be blank.")
        return cleaned


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    asset_count: int = 0
    message_count: int = 0


# --- Assets ---------------------------------------------------------------
class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: Literal["image", "pdf", "text"]
    filename: str
    mime: str
    size_bytes: int
    page_count: int
    has_text: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Messages -------------------------------------------------------------
class Citation(BaseModel):
    asset_id: str
    filename: str
    page: int | None = None
    snippet: str
    score: float


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Question cannot be blank.")
        return cleaned


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: Literal["user", "assistant"]
    content: str
    provider: str
    grounded: bool
    citations: list[Citation] = Field(default_factory=list)
    latency_ms: float
    created_at: datetime


class HealthOut(BaseModel):
    status: Literal["ok"]
    app: str
    version: str
    provider: str
    database: str
