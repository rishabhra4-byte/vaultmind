import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    # Sign people out after this long without a request (0 turns it off). Sessions also end at
    # sign-out, on a password change or deactivation, and when an administrator ends them.
    session_idle_minutes: int = Field(120, ge=0, le=60 * 24 * 30)
    # Scheduled backups (app/backup.py): off until a folder is set. Each one is restored into a
    # scratch database to check it, and only the newest `backup_keep` are kept.
    backup_dir: str = ""
    backup_interval_hours: int = Field(24, ge=1, le=24 * 31)
    backup_keep: int = Field(14, ge=1, le=1000)
    # Malware scanning with ClamAV (app/malware.py): clamd's "host:port" or socket path. While
    # set, files are refused when clamd can't scan them, unless clamav_required is turned off.
    clamav_address: str = ""
    clamav_required: bool = True
    clamav_timeout_seconds: float = Field(30.0, gt=0, le=600)

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

    # The address people use to reach VaultMind, e.g. https://vaultmind.example.com. Used for
    # links in emails and Slack messages, and for the sign-in redirect with Google/Microsoft.
    public_url: str = ""

    # Background jobs: syncing connected sources, reminders, email/Slack delivery.
    scheduler_enabled: bool = True
    connector_timeout_seconds: float = 30.0

    # Shared-folder connector: the only folders (and their subfolders) an admin may connect.
    folder_roots: list[str] = []

    # Google Workspace: a service account with domain-wide delegation reads Drive files, their
    # sharing and (optionally) the directory, acting as `google_admin_email`.
    google_service_account_file: str = ""
    google_service_account_json: str = ""
    google_admin_email: str = ""
    google_api_base: str = "https://www.googleapis.com"
    google_admin_api_base: str = "https://admin.googleapis.com"
    google_token_url: str = "https://oauth2.googleapis.com/token"

    # Microsoft 365: an app registration (client credentials) with Files.Read.All / Sites.Read.All
    # and, for directory sync, User.Read.All + GroupMember.Read.All application permissions.
    microsoft_tenant_id: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_graph_base: str = "https://graph.microsoft.com/v1.0"
    microsoft_login_base: str = "https://login.microsoftonline.com"

    # Sign in with Google / Microsoft (OpenID Connect). Google needs an OAuth client (not the
    # service account); Microsoft reuses the app registration above, with a redirect URI added.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_issuer: str = "https://accounts.google.com"
    # Company domains whose Google accounts may sign in (and get an account on first sign-in).
    google_allowed_domains: list[str] = []
    microsoft_sso: bool = False  # turn on to show "Continue with Microsoft"
    # Create an account the first time someone from an allowed domain / the tenant signs in.
    sso_auto_create: bool = True
    sso_default_team: str = ""  # team for people created at sign-in (default: the first team)
    # Turn off once everyone signs in with their company account. Admins keep password
    # sign-in as a break-glass route in case the identity provider is down.
    password_login: bool = True

    # Email copies of notifications (optional).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Slack: the bot (slash command, mentions, direct messages) and notification copies.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_api_base: str = "https://slack.com/api"

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_bot_token and self.slack_signing_secret)

    def link(self, path: str) -> str:
        """An absolute link to a page, for emails and Slack (relative if the public URL isn't set)."""
        return f"{self.public_url.rstrip('/')}{path}" if self.public_url else path

    @property
    def google_sso_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def microsoft_sso_configured(self) -> bool:
        return self.microsoft_sso and self.microsoft_configured

    @property
    def google_configured(self) -> bool:
        return bool((self.google_service_account_file or self.google_service_account_json) and self.google_admin_email)

    @property
    def microsoft_configured(self) -> bool:
        return bool(self.microsoft_tenant_id and self.microsoft_client_id and self.microsoft_client_secret)

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
