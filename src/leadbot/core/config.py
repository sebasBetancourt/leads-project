"""Configuración: secretos desde .env (Settings) y reglas de negocio desde config/*.yaml."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carpeta con config/, knowledge/ y output/: la raíz del repo, o /app en Docker.
ROOT_DIR = Path(os.environ.get("LEADBOT_HOME", Path.cwd()))
CONFIG_DIR = ROOT_DIR / "config"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
OUTPUT_DIR = ROOT_DIR / "output"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://leadbot:leadbot@localhost:5432/leadbot"
    gmaps_scraper_url: str = "http://localhost:8080"

    llm_model: str = "anthropic/claude-sonnet-5-5"

    web_search_provider: str = "duckduckgo"
    serpapi_api_key: str = ""

    google_service_account_file: str = "credentials/google-service-account.json"
    google_sheet_id: str = ""

    gmail_address: str = ""
    gmail_app_password: str = ""
    sender_name: str = "BrainSol"

    pause_all: bool = False
    pause_email: bool = False
    pause_whatsapp: bool = False
    pause_instagram: bool = False
    pause_facebook: bool = False
    pause_linkedin: bool = False

    def is_paused(self, channel: str) -> bool:
        return self.pause_all or bool(getattr(self, f"pause_{channel}", False))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    with open(config_dir / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
