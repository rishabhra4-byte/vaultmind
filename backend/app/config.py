from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix VAULTMIND_)."""

    model_config = SettingsConfigDict(env_prefix="VAULTMIND_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./vaultmind.db"
    storage_dir: str = "./storage"

    jwt_secret: str = "dev-only-secret-change-me-in-production-0000"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 8

    max_upload_mb: int = 20
    cors_origins: list[str] = ["http://localhost:5173"]

    # AI assistant. With no API key the assistant runs in offline mode:
    # same permission-filtered retrieval, but answers are built from snippets instead of an LLM.
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 1024
    retrieval_top_k: int = 6

    seed_demo_data: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
