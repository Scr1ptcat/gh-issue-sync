from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level configuration (stateless service; per-request overrides allowed)."""
    gh_token: str = Field(default="", alias="GH_TOKEN")
    default_owner: str = Field(default="USEPA", alias="OWNER")
    default_repo: str = Field(default="AIRules", alias="REPO")
    default_project_title: str = Field(default="AIRules Baseline Alignment", alias="PROJECT_TITLE")
    request_timeout_seconds: float = 20.0
    max_retries: int = 5
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def get_settings() -> Settings:
    return Settings()
