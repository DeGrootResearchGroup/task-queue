from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Core
    secret_key: str = "dev-secret-key-change-me"
    database_url: str = "sqlite:///./task_queue.db"
    environment: str = "development"

    # Owner bootstrap (env-var based, single owner in v1)
    owner_username: str = "owner"
    owner_password_hash: str = ""

    # Quick Action threshold (label only; not enforced programmatically)
    quick_action_minutes: int = 10

    # Desired-date urgency thresholds, in days remaining (spec §50)
    urgency_green_days: int = 7  # more than this many days remaining -> green
    urgency_red_days: int = 2  # this many days remaining (or fewer/overdue) -> red

    # Anti-spam
    min_form_fill_seconds: float = 3.0
    submit_rate_limit: str = "5/hour"

    # Brute-force protection on Owner login, keyed by IP
    login_rate_limit: str = "10/minute"

    # Owner-facing settings defaults (overridable via /settings, stored in AppSettings row)
    owner_display_name: str = "The Owner"
    meeting_booking_url: str = ""
    meeting_booking_text: str = (
        "Need to schedule a meeting instead? Use the normal booking process."
    )

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
