"""FastAPI dependencies: authentication and organization/project authorization."""
from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import Unauthorized
from app.core.security import decode_access_token
from app.db import get_db
from app.models import Membership, Organization, Project, User
from app.services.core import Access, membership_for, project_access

bearer = HTTPBearer(auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if not credentials or not credentials.credentials:
        raise Unauthorized("Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise Unauthorized(f"Invalid or expired token: {exc}") from exc
    user = db.get(User, payload.get("sub"))
    if not user:
        raise Unauthorized("User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def current_access(
    db: DbSession,
    user: CurrentUser,
    organization_header: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
) -> Access:
    membership, organization = membership_for(db, user, organization_header)
    return Access(user=user, organization=organization, role=membership.role)


CurrentAccess = Annotated[Access, Depends(current_access)]


def require_project(
    project_id: str,
    db: DbSession,
    user: CurrentUser,
    min_role: str = "viewer",
) -> Access:
    return project_access(db, user, project_id, min_role)


def project_dep(min_role: str = "viewer"):
    def dependency(project_id: str, db: DbSession, user: CurrentUser) -> Access:
        return project_access(db, user, project_id, min_role)

    return dependency
