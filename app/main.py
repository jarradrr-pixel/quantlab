"""Application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api import routes_auth, routes_broker, routes_dashboard
from app.brokers.alpaca import AlpacaPaperBroker
from app.config import Settings, get_settings
from app.core.audit import AuditCategory, AuditLog
from app.db.base import Base
from app.db.models import KILL_SWITCH_KEY, Operator, SystemFlag
from app.db.session import get_engine, session_scope
from app.logging_config import configure_logging
from app.rate_limit import RateLimiter
from app.security import SessionManager, hash_password
from app.web.templating import templates

logger = logging.getLogger(__name__)


def bootstrap_database(settings: Settings) -> None:
    """Create tables on the SQLite fallback and seed required rows.

    On PostgreSQL, schema creation belongs to Alembic; this function only seeds.
    """
    if settings.is_sqlite:
        Base.metadata.create_all(bind=get_engine())

    with session_scope() as session:
        flag = session.get(SystemFlag, KILL_SWITCH_KEY)
        if flag is None:
            session.add(
                SystemFlag(
                    key=KILL_SWITCH_KEY,
                    value=settings.kill_switch_engaged_on_boot,
                    updated_by="system",
                    reason="Initial value at first start-up. The system fails closed.",
                )
            )
            AuditLog(session).record(
                category=AuditCategory.SYSTEM,
                action="kill_switch_initialised",
                actor="system",
                payload={"engaged": settings.kill_switch_engaged_on_boot},
            )

        if settings.bootstrap_operator_email and settings.bootstrap_operator_password:
            email = settings.bootstrap_operator_email.strip().lower()
            existing = session.query(Operator).filter(Operator.email == email).first()
            if existing is None:
                session.add(
                    Operator(
                        email=email,
                        display_name=email.split("@")[0],
                        password_hash=hash_password(
                            settings.bootstrap_operator_password.get_secret_value()
                        ),
                    )
                )
                AuditLog(session).record(
                    category=AuditCategory.SYSTEM,
                    action="bootstrap_operator_created",
                    actor="system",
                    payload={"email": email},
                )
                logger.info("bootstrap operator created")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting",
        extra={
            "context": {
                "environment": settings.environment.value,
                "trading_mode": settings.trading_mode.value,
            }
        },
    )
    app.state.session_manager = SessionManager(
        secret_key=settings.secret_key.get_secret_value(),
        max_age_seconds=settings.session_max_age_seconds,
    )
    bootstrap_database(settings)
    yield
    broker = getattr(app.state, "broker_adapter", None)
    if isinstance(broker, AlpacaPaperBroker):
        broker.close()
    logger.info("shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="QuantLab",
        description=(
            "AI-assisted quantitative research and paper-trading system. "
            "Research and paper trading only; no live brokerage connection."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment.value != "production" else None,
    )

    app.state.login_limiter = RateLimiter(settings.login_rate_limit)

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    app.include_router(routes_auth.router)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_broker.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        """Redirect browsers to the sign-in page; return JSON to API clients."""
        if exc.status_code == status.HTTP_401_UNAUTHORIZED and _wants_html(request):
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        if _wants_html(request):
            return templates.TemplateResponse(
                request,
                "base.html",
                {"title": "Error", "operator": None},
                status_code=exc.status_code,
                headers={"X-Error-Detail": str(exc.detail)[:200]},
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "request validation failed",
            extra={"context": {"errors": len(exc.errors())}},
        )
        return JSONResponse(
            {"detail": "The submitted data is not valid."},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_exception_handler(
        _request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        """Never leak driver messages, which can contain connection strings."""
        logger.exception("database error", exc_info=exc)
        return JSONResponse(
            {"detail": "A database error occurred. The action was not applied."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return app


def _wants_html(request: Request) -> bool:
    """Whether to answer with a page rather than JSON.

    Browsers send ``text/html``; API clients that want JSON must ask for it.
    Anything else (including a bare ``*/*``) is treated as a browser, so a
    signed-out visitor lands on the sign-in page instead of a JSON blob.
    """
    accept = request.headers.get("accept", "")
    return "application/json" not in accept


app = create_app()
