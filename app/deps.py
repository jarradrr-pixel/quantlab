"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

import anthropic
from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agents.base import (
    HypothesisAgent,
    KnowledgeTestAgent,
    ResearchAgent,
    StrategyCodeAgent,
)
from app.agents.claude import ClaudeResearchAgent
from app.agents.hypothesis import ClaudeHypothesisAgent
from app.agents.knowledge_test import ClaudeKnowledgeTestAgent
from app.agents.strategy_code import ClaudeStrategyCodeAgent
from app.brokers.alpaca import AlpacaPaperBroker
from app.brokers.base import BrokerAdapter
from app.brokers.mock import MockBroker
from app.config import Settings, get_settings
from app.db.models import Operator
from app.db.session import get_db
from app.marketdata.base import MarketDataProvider
from app.marketdata.service import MarketDataService
from app.marketdata.yfinance_provider import YFinanceProvider
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


def get_broker(request: Request, settings: SettingsDep, db: DbDep) -> BrokerAdapter:
    """Return the active broker adapter -- there is always one.

    Explicit ``settings.broker_backend`` selects it, never inferred from
    credential presence (see ``Settings._alpaca_backend_requires_credentials``):
    a typo'd Alpaca key should fail start-up, not silently degrade to mock
    simulation.
    """
    if settings.broker_backend == "mock":
        # Cheap to build, no network client to reuse, and `db` is per-request
        # anyway -- unlike AlpacaPaperBroker, nothing here is worth caching.
        return MockBroker(db)

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


BrokerDep = Annotated[BrokerAdapter, Depends(get_broker)]


def get_market_data_provider() -> MarketDataProvider:
    return YFinanceProvider()


MarketDataProviderDep = Annotated[MarketDataProvider, Depends(get_market_data_provider)]


def get_market_data_service(
    db: DbDep, provider: MarketDataProviderDep
) -> MarketDataService:
    return MarketDataService(db, provider)


MarketDataDep = Annotated[MarketDataService, Depends(get_market_data_service)]


def _get_anthropic_client(
    request: Request, settings: SettingsDep
) -> anthropic.Anthropic | None:
    """Return the shared Anthropic client, or None if no API key is
    configured. Shared across every agent below so they don't each open their
    own HTTP connection pool for the one credential they all use.
    """
    if settings.anthropic_api_key is None:
        return None
    cached = getattr(request.app.state, "anthropic_client", None)
    if cached is None:
        cached = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        request.app.state.anthropic_client = cached
    return cached


def get_research_agent(
    request: Request,
    settings: SettingsDep,
    client: Annotated[anthropic.Anthropic | None, Depends(_get_anthropic_client)],
) -> ResearchAgent | None:
    """Return the research agent, or None if no API key is configured.

    Unlike the broker (which always has a safe ``mock`` backend), there is no
    coherent "simulate a paid third-party LLM for free" substitute -- absence
    of a key means the feature is unavailable, and routes must treat ``None``
    as a 409, not silently degrade.
    """
    if client is None:
        return None
    assert settings.anthropic_api_key is not None  # narrowed by client not being None
    cached = getattr(request.app.state, "research_agent", None)
    if cached is None:
        cached = ClaudeResearchAgent(
            api_key=settings.anthropic_api_key.get_secret_value(), client=client
        )
        request.app.state.research_agent = cached
    return cached


ResearchAgentDep = Annotated[ResearchAgent | None, Depends(get_research_agent)]


def get_knowledge_test_agent(
    request: Request,
    settings: SettingsDep,
    client: Annotated[anthropic.Anthropic | None, Depends(_get_anthropic_client)],
) -> KnowledgeTestAgent | None:
    """Same "no mock substitute, None means unconfigured" pattern as
    ``get_research_agent``."""
    if client is None:
        return None
    assert settings.anthropic_api_key is not None  # narrowed by client not being None
    cached = getattr(request.app.state, "knowledge_test_agent", None)
    if cached is None:
        cached = ClaudeKnowledgeTestAgent(
            api_key=settings.anthropic_api_key.get_secret_value(), client=client
        )
        request.app.state.knowledge_test_agent = cached
    return cached


KnowledgeTestAgentDep = Annotated[
    KnowledgeTestAgent | None, Depends(get_knowledge_test_agent)
]


def get_hypothesis_agent(
    request: Request,
    settings: SettingsDep,
    client: Annotated[anthropic.Anthropic | None, Depends(_get_anthropic_client)],
) -> HypothesisAgent | None:
    if client is None:
        return None
    assert settings.anthropic_api_key is not None  # narrowed by client not being None
    cached = getattr(request.app.state, "hypothesis_agent", None)
    if cached is None:
        cached = ClaudeHypothesisAgent(
            api_key=settings.anthropic_api_key.get_secret_value(), client=client
        )
        request.app.state.hypothesis_agent = cached
    return cached


HypothesisAgentDep = Annotated[HypothesisAgent | None, Depends(get_hypothesis_agent)]


def get_strategy_code_agent(
    request: Request,
    settings: SettingsDep,
    client: Annotated[anthropic.Anthropic | None, Depends(_get_anthropic_client)],
) -> StrategyCodeAgent | None:
    if client is None:
        return None
    assert settings.anthropic_api_key is not None  # narrowed by client not being None
    cached = getattr(request.app.state, "strategy_code_agent", None)
    if cached is None:
        cached = ClaudeStrategyCodeAgent(
            api_key=settings.anthropic_api_key.get_secret_value(), client=client
        )
        request.app.state.strategy_code_agent = cached
    return cached


StrategyCodeAgentDep = Annotated[
    StrategyCodeAgent | None, Depends(get_strategy_code_agent)
]
