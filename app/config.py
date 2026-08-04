"""Application configuration.

All settings load from environment variables (or a local ``.env`` file that is
never committed). Defaults are chosen to be safe rather than convenient: the
application refuses to start in an ambiguous or unsafe configuration rather
than guessing. See ``docs/environment.md``.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from typing import Final, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ALPACA_PAPER_HOST: Final[str] = "paper-api.alpaca.markets"


class Environment(StrEnum):
    """Deployment environment. Controls how strict start-up validation is."""

    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class TradingMode(StrEnum):
    """Trading mode.

    Only ``PAPER`` is implemented. ``LIVE`` exists solely so that the value is
    rejected explicitly and loudly instead of being silently unrecognised.
    """

    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    """Runtime settings, validated once at start-up."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="QUANTLAB_",
        extra="forbid",
        frozen=True,
    )

    # --- Core -----------------------------------------------------------
    environment: Environment = Environment.LOCAL
    debug: bool = False
    app_name: str = "QuantLab"

    # --- Safety ---------------------------------------------------------
    trading_mode: TradingMode = TradingMode.PAPER
    """Hard-coded to paper. Live trading is not implemented and is rejected."""

    kill_switch_engaged_on_boot: bool = True
    """Fail closed: the system boots halted and an operator must release it."""

    require_human_approval: bool = True
    """May not be disabled outside the test environment."""

    # --- Database -------------------------------------------------------
    database_url: str = "sqlite+pysqlite:///./quantlab.db"
    database_echo: bool = False

    # --- Web / session --------------------------------------------------
    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(48))
    )
    session_cookie_name: str = "quantlab_session"
    session_max_age_seconds: int = 60 * 60 * 8
    session_cookie_secure: bool = True
    cors_allowed_origins: list[str] = Field(default_factory=list)
    """Empty by default. Same-origin server-rendered UI needs no CORS."""

    # --- Rate limiting --------------------------------------------------
    login_rate_limit: str = "5/minute"
    api_rate_limit: str = "120/minute"

    # --- Account lockout --------------------------------------------------
    login_lockout_threshold: int = 5
    """Consecutive wrong-password attempts against one operator before it locks."""
    login_lockout_duration_seconds: int = 900
    """Persisted on the Operator row -- unlike login_rate_limit's in-memory,
    per-process counter, this survives a restart or a second worker."""

    # --- Bootstrap operator --------------------------------------------
    bootstrap_operator_email: str | None = None
    bootstrap_operator_password: SecretStr | None = None

    # --- Strategy guard rails (MVP defaults, configurable) --------------
    allowed_symbols: list[str] = Field(default_factory=lambda: ["SPY"])
    allowed_timeframes: list[str] = Field(default_factory=lambda: ["1Day"])
    max_position_percentage: float = 2.0
    max_total_exposure_percentage: float = 10.0
    max_daily_loss_percentage: float = 1.0
    max_orders_per_day: int = 2
    max_open_positions: int = 1
    allow_shorting: bool = False
    allow_leverage: bool = False

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Broker (Phase 3) ------------------------------------------------
    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    alpaca_paper_base_url: str = f"https://{_ALPACA_PAPER_HOST}"

    # --- Broker backend (Phase 2) ----------------------------------------
    broker_backend: Literal["mock", "alpaca"] = "mock"
    """Which BrokerAdapter services paper trading. Explicit, not inferred from
    credential presence -- a typo'd Alpaca key should fail start-up, not
    silently degrade to mock simulation."""

    # --- Research agent (Phase 4) -----------------------------------------
    anthropic_api_key: SecretStr | None = None
    """Unset means the research agent is unavailable -- there is no safe
    mock substitute for a paid LLM call, unlike the broker's mock backend."""

    @field_validator("trading_mode")
    @classmethod
    def _reject_live_trading(cls, value: TradingMode) -> TradingMode:
        if value is not TradingMode.PAPER:
            raise ValueError(
                "Live trading is not implemented. QUANTLAB_TRADING_MODE must be 'paper'."
            )
        return value

    @field_validator("allowed_symbols")
    @classmethod
    def _normalise_symbols(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("allowed_symbols must not be empty; the allowlist is mandatory.")
        return [symbol.strip().upper() for symbol in value]

    @field_validator(
        "max_position_percentage",
        "max_total_exposure_percentage",
        "max_daily_loss_percentage",
    )
    @classmethod
    def _percentages_in_range(cls, value: float) -> float:
        if not 0 < value <= 100:
            raise ValueError("percentage limits must be greater than 0 and at most 100")
        return value

    @field_validator("login_lockout_threshold", "login_lockout_duration_seconds")
    @classmethod
    def _positive_lockout_setting(cls, value: int) -> int:
        if value < 1:
            raise ValueError("login lockout settings must be at least 1")
        return value

    @field_validator("alpaca_paper_base_url")
    @classmethod
    def _reject_non_paper_alpaca_host(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != _ALPACA_PAPER_HOST:
            raise ValueError(
                f"alpaca_paper_base_url must be https://{_ALPACA_PAPER_HOST}. "
                "Live trading is not implemented."
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def _alpaca_credentials_paired(self) -> Settings:
        if (self.alpaca_api_key is None) != (self.alpaca_api_secret is None):
            raise ValueError(
                "alpaca_api_key and alpaca_api_secret must both be set, or neither."
            )
        return self

    @model_validator(mode="after")
    def _alpaca_backend_requires_credentials(self) -> Settings:
        if self.broker_backend == "alpaca" and not self.broker_configured:
            raise ValueError(
                "broker_backend='alpaca' requires alpaca_api_key and alpaca_api_secret "
                "to both be set."
            )
        return self

    @model_validator(mode="after")
    def _enforce_production_strictness(self) -> Settings:
        if self.environment is Environment.PRODUCTION:
            if self.debug:
                raise ValueError("debug must be disabled in production")
            if not self.session_cookie_secure:
                raise ValueError("session_cookie_secure must remain enabled in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is a local-development fallback only")
        if not self.require_human_approval and self.environment is not Environment.TEST:
            raise ValueError(
                "require_human_approval may only be disabled in the test environment"
            )
        if self.max_position_percentage > self.max_total_exposure_percentage:
            raise ValueError(
                "max_position_percentage cannot exceed max_total_exposure_percentage"
            )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def broker_configured(self) -> bool:
        return self.alpaca_api_key is not None and self.alpaca_api_secret is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
