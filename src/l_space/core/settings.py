"""Central configuration. Nothing else in the codebase should hard-code paths or model names."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="L_SPACE_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/l-space.db")
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    # Generous by default: a first request on CPU has to load the model.
    ollama_timeout: float = 300.0
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
