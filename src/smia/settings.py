"""Runtime settings, read from environment / .env. Keys never live in code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = Field(..., alias="DATABASE_URL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    scrapecreators_api_key: str | None = Field(default=None, alias="SCRAPECREATORS_API_KEY")
    slack_webhook_url: str | None = Field(default=None, alias="SLACK_WEBHOOK_URL")
    slack_bot_token: str | None = Field(default=None, alias="SLACK_BOT_TOKEN")  # xoxb-
    slack_app_token: str | None = Field(default=None, alias="SLACK_APP_TOKEN")  # xapp-, Socket Mode

    analyst_model: str = Field(default="claude-opus-5", alias="SMIA_ANALYST_MODEL")
    labeling_model: str = Field(default="claude-haiku-4-5", alias="SMIA_LABELING_MODEL")

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, v: str) -> str:
        # Neon hands out postgresql://; SQLAlchemy needs the psycopg3 driver named.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://") :]
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://") :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def load_thresholds() -> dict[str, Any]:
    """Versioned decision thresholds. Stamped into every agent run."""
    with (CONFIG_DIR / "thresholds.yaml").open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if "version" not in data:
        raise ValueError("config/thresholds.yaml must declare a version")
    return data


@lru_cache
def load_dimensions() -> dict[str, Any]:
    """Promoted labelling dimensions. Starts with format only."""
    with (CONFIG_DIR / "dimensions.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
