"""Test fixtures.

Every test runs against a fresh file-backed SQLite database in a temporary
directory, so tests are order-independent and never touch a real database.
No test contacts a broker, real or paper.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.base import Base
from app.db.models import KILL_SWITCH_KEY, Operator, Project, SystemFlag
from app.db.session import create_db_engine
from app.security import hash_password

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Test settings pointing at a throwaway SQLite file."""
    return Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key-not-used-anywhere-real",
        session_cookie_secure=False,
        kill_switch_engaged_on_boot=True,
        login_rate_limit="1000/minute",
        # Explicitly unset rather than inherited: a real key in the developer's
        # own .env must never let tests reach the live Anthropic API.
        anthropic_api_key=None,
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    session.add(
        SystemFlag(
            key=KILL_SWITCH_KEY,
            value=True,
            updated_by="system",
            reason="test bootstrap",
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def operator(db: Session) -> Operator:
    record = Operator(
        email="operator@example.test",
        display_name="Test Operator",
        password_hash=hash_password(TEST_PASSWORD),
    )
    db.add(record)
    db.commit()
    return record


@pytest.fixture
def project(db: Session) -> Project:
    record = Project(name="Test project", research_objective="Testing the machine.")
    db.add(record)
    db.commit()
    return record


@pytest.fixture
def release_kill_switch(db: Session) -> None:
    flag = db.get(SystemFlag, KILL_SWITCH_KEY)
    assert flag is not None
    flag.value = False
    db.commit()


@pytest.fixture
def client(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A TestClient wired to the temporary database.

    ``get_settings`` is cached, so the cache is cleared and the environment is
    populated before the application is constructed. The developer's own
    ``.env`` is never a valid input here -- a real key left in it (e.g.
    ``QUANTLAB_ANTHROPIC_API_KEY``) must not leak into what's meant to be an
    isolated settings environment, so its ``env_file`` is disabled for the
    duration of this fixture and every field the tests rely on is set
    explicitly below instead.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("QUANTLAB_ENVIRONMENT", "test")
    monkeypatch.setenv("QUANTLAB_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("QUANTLAB_SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv("QUANTLAB_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("QUANTLAB_BOOTSTRAP_OPERATOR_EMAIL", "operator@example.test")
    monkeypatch.setenv("QUANTLAB_BOOTSTRAP_OPERATOR_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("QUANTLAB_LOGIN_RATE_LIMIT", "1000/minute")

    import app.db.session as session_module

    get_settings.cache_clear()
    session_module.reset_engine()

    from app.main import create_app

    application = create_app(get_settings())
    with TestClient(application) as test_client:
        yield test_client

    get_settings.cache_clear()
    session_module.reset_engine()


@pytest.fixture
def signed_in_client(client: TestClient) -> TestClient:
    response = client.post(
        "/login",
        data={"email": "operator@example.test", "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client


def csrf_from(html: str) -> str:
    """Pull the CSRF token out of a rendered form."""
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


os.environ.setdefault("QUANTLAB_ENVIRONMENT", "test")
