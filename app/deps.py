"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.brokers.alpaca import AlpacaPaperBroker
from app.brokers.base import BrokerAdapter
from app.config import Settings, get_settings
from app.db.models import Operator
from app.db.session import get_db
from app.security import CSRF_FIELD_NAME, SessionData, SessionManager, csrf_tokens_match

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def get_session_manager(request: Request) -> SessionManager:
    manager = getattr(request.app.state, "session_manager", None)
    if not isinstance(manager, SessionManager):  # pragma: no cover - config error
        raise RuntimeError("session manager is not configured on the application")
    return manager


def get_session_data(
    request: Request,
    settings: SettingsDep,
    manager: Annotated[SessionManager, Depends(get_session_manager)],
) -> SessionData | None:
    cookie = request.cookies.get(settings.session_cookie_name)
    return manager.read(cookie)


def get_current_operator(
    session_data: Annotated[SessionData | None, Depends(get_session_data)],
    db: DbDep,
) -> Operator | None:
    """Resolve the signed-in operator, or None. Never raises."""
    if session_data is None:
        return None
    operator = db.get(Operator, session_data.operator_id)
    if operator is None or not operator.is_active:
        return None
    return operator


def require_operator(
    operator: Annotated[Operator | None, Depends(get_current_operator)],
) -> Operator:
    """Require an authenticated, active operator."""
    if operator is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"Location": "/login"},
        )
    return operator


def require_csrf(
    session_data: Annotated[SessionData | None, Depends(get_session_data)],
    csrf_token: Annotated[str, Form(alias=CSRF_FIELD_NAME)] = "",
) -> None:
    """Validate the submitted CSRF token against the session token."""
    submitted = csrf_token
    if session_data is None or not csrf_tokens_match(session_data.csrf_token, submitted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This form has expired. Reload the page and try again.",
        )


OperatorDep = Annotated[Operator, Depends(require_operator)]


def get_broker(request: Request, settings: SettingsDep) -> BrokerAdapter | None:
    """Return the cached broker adapter, or None if Alpaca isn't configured."""
    if not settings.broker_configured:
        return None
    cached = getattr(request.app.state, "broker_adapter", None)
    if cached is None:
        assert settings.alpaca_api_key is not None  # narrowed by broker_configured
        assert settings.alpaca_api_secret is not None
        cached = AlpacaPaperBroker(
            api_key=settings.alpaca_api_key.get_secret_value(),
            api_secret=settings.alpaca_api_secret.get_secret_value(),
            base_url=settings.alpaca_paper_base_url,
        )
        request.app.state.broker_adapter = cached
    return cached


BrokerDep = Annotated[BrokerAdapter | None, Depends(get_broker)]
