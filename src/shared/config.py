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
    default_language: str = "en"
    default_market: str = "us"
    playwright_headless: bool = True
    browser_ws_endpoint: str = ""  # WebSocket URL for remote browser (e.g. ws://localhost:3000)
    geoip_db_path: str = "data/GeoLite2-Country.mmdb"
    otel_exporter_endpoint: str = ""
    phoenix_enabled: bool = True
    phoenix_port: int = 6006
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    scraper_llm_model: str = ""  # Override for scraper strategy discovery; falls back to llm_model
    proxy_url: str = ""  # Residential proxy URL template with {country} placeholder, e.g. http://user-country-{country}:pass@proxy.example.com:22225
    searxng_url: str = "http://localhost:8888"  # SearXNG meta-search instance URL

    model_config = {"env_file": "config/.env.local", "extra": "ignore"}


settings = Settings()
