"""Sign-in and sign-out."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.audit import AuditCategory, AuditLog
from app.db.base import utcnow
from app.db.models import Operator
from app.deps import DbDep, SettingsDep, get_session_manager
from app.rate_limit import client_key
from app.security import SessionManager, verify_password
from app.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

_GENERIC_FAILURE = "Email or password is incorrect."


@router.get("/login")
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "title": "Sign in"}
    )


@router.post("/login")
def login(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    manager: Annotated[SessionManager, Depends(get_session_manager)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    """Authenticate an operator.

    Failures are indistinguishable to the caller whether the account is
    unknown, inactive or the password is wrong.
    """
    limiter = request.app.state.login_limiter
    key = client_key(request.client.host if request.client else None)
    if not limiter.allow(key):
        audit = AuditLog(db)
        audit.record(
            category=AuditCategory.AUTH,
            action="login_rate_limited",
            actor="anonymous",
            payload={"outcome": "throttled"},
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many attempts. Wait a moment and try again.", "title": "Sign in"},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    normalised = email.strip().lower()
    operator = db.scalars(
        select(Operator).where(Operator.email == normalised)
    ).first()

    authenticated = (
        operator is not None
        and operator.is_active
        and verify_password(operator.password_hash, password)
    )

    audit = AuditLog(db)
    if not authenticated or operator is None:
        audit.record(
            category=AuditCategory.AUTH,
            action="login_failed",
            actor=normalised,
            payload={"outcome": "rejected"},
        )
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _GENERIC_FAILURE, "title": "Sign in"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    operator.last_login_at = utcnow()
    cookie_value, _ = manager.issue(operator.id)
    audit.record(
        category=AuditCategory.AUTH,
        action="login_succeeded",
        actor=operator.email,
        payload={"operator_id": operator.id},
    )
    db.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        settings.session_cookie_name,
        cookie_value,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
def logout(db: DbDep, settings: SettingsDep, request: Request) -> Response:
    AuditLog(db).record(
        category=AuditCategory.AUTH, action="logout", actor="operator", payload={}
    )
    db.commit()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response
