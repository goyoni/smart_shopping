"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "local"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./smart_shopping.db"
    log_level: str = "DEBUG"
    log_format: str = "console"
    playwright_headless: bool = True
    geoip_db_path: str = "data/GeoLite2-Country.mmdb"
    otel_exporter_endpoint: str = ""
    phoenix_enabled: bool = True
    phoenix_port: int = 6006

    model_config = {"env_file": "config/.env.local", "extra": "ignore"}


settings = Settings()
