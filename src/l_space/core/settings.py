"""Central configuration. Nothing else in the codebase should hard-code paths or model names."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="L_SPACE_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/l-space.db")
    # A full SQLAlchemy database URL. When set it wins over `db_path`; otherwise the URL is built
    # from `db_path` (SQLite).
    database_url: str | None = None
    # Seconds a request may wait for the database connection. SQLite runs behind a pool of one
    # (MULTI_USER_TODO.md, D8), so this bounds how long one slow call can stall the others.
    db_pool_timeout: float = 30.0
    # Bring the database schema up to date when the app or CLI starts. Turn off where migrations
    # are run as a separate deploy step (`l-space migrate`); the app then refuses to start on
    # an out-of-date database instead of failing on the first query.
    auto_migrate: bool = True
    # Name of the account that takes over an existing library when the database first gets users.
    # Only used by that one migration; changing it later has no effect.
    default_owner: str = "owner"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    # Optional larger model for routing only. Routing is a short, accuracy-critical call, while
    # writing is the slow one, so a bigger router costs far less than a bigger writer.
    ollama_router_model: str | None = None
    # Generous by default: a first request on CPU has to load the model.
    ollama_timeout: float = 300.0
    log_level: str = "INFO"

    def sqlalchemy_url(self) -> URL:
        """The database URL: ``database_url`` if set, otherwise SQLite at ``db_path``."""
        if self.database_url:
            return make_url(self.database_url)
        return URL.create("sqlite", database=str(self.db_path))


def get_settings() -> Settings:
    return Settings()
