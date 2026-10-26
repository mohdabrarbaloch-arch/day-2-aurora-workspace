"""Shared FastAPI dependencies: auth resolution and ownership scoping.

The ownership helper is the security boundary for the whole API. It returns
**404, not 403**, when a workspace belongs to someone else: a 403 would confirm
the row exists, turning an ID probe into a directory of other users' work.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, Workspace
from app.security import AuthError, decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    """Resolve the bearer token to a User row."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token.")

    user = db.get(User, user_id)
    if user is None:
        # The account was deleted after the token was issued.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account no longer exists."
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_owned_workspace(
    workspace_id: str,
    user: CurrentUser,
    db: DbSession,
) -> Workspace:
    """Fetch a workspace the caller owns, or 404."""
    workspace = db.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_id == user.id)
    )
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
    return workspace


OwnedWorkspace = Annotated[Workspace, Depends(get_owned_workspace)]


def get_request_id(request: Request) -> str:
    return request.headers.get("x-request-id", "-")
