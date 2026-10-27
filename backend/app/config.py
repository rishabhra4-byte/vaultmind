import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("vaultmind.config")

# Values that must never be used as a signing secret, e.g. copied from docs or old defaults.
WEAK_SECRETS = {
    "",
    "change-me",
    "change-me-in-production",
    "dev-only-secret-change-me-in-production-0000",
    "please-set-a-long-random-secret-in-env",
}


class ConfigError(RuntimeError):
    pass


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix VAULTMIND_)."""

    model_config = SettingsConfigDict(env_prefix="VAULTMIND_", env_file=".env", extra="ignore")

    # "production" turns on strict checks: a real JWT secret is required, demo data is off
    # unless explicitly enabled, and the interactive API docs are hidden.
    env: Literal["development", "production"] = "development"

    database_url: str = "sqlite:///./vaultmind.db"
    storage_dir: str = "./storage"

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 8

    max_upload_mb: int = 20
    cors_origins: list[str] = ["http://localhost:5173"]
    hsts: bool = False  # enable when served over HTTPS

    # AI assistant. With no API key the assistant runs in offline mode:
    # same permission-filtered retrieval, but answers are built from snippets instead of an LLM.
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""  # optional: a gateway/proxy, or a local fake for tests
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    retrieval_top_k: int = 6

    # None = follow the environment: on in development, off in production.
    seed_demo_data: bool | None = None

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def demo_enabled(self) -> bool:
        return (not self.is_production) if self.seed_demo_data is None else self.seed_demo_data

    def validate_for_startup(self) -> None:
        """Fail fast on unsafe production configuration; patch up development defaults."""
        if self.jwt_secret in WEAK_SECRETS or len(self.jwt_secret) < 32:
            if self.is_production:
                raise ConfigError(
                    "VAULTMIND_JWT_SECRET must be set to a random value of at least 32 characters in production "
                    "(for example: openssl rand -hex 32)."
                )
            # Development: a random per-process secret is safe; sign-ins just reset on restart.
            self.jwt_secret = secrets.token_urlsafe(48)
            log.warning("No VAULTMIND_JWT_SECRET set; using a random one. Sessions end when the server restarts.")
        if self.is_production and self.demo_enabled:
            log.warning(
                "DEMO DATA IS ENABLED IN PRODUCTION: nine demo accounts, including an administrator, "
                "share a public password. Use this only for a public demo, never with real documents."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
