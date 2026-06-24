"""
SGOS Backend — Centralized Configuration
All settings validated at startup via pydantic-settings.
Env vars use SGOS_ prefix (e.g., SGOS_API_KEY, SGOS_LLM_TIMEOUT).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — validated at import time."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8420
    api_key: str = ""  # Empty = auth disabled (dev mode)
    debug: bool = False
    version: str = "0.1.0"

    # Database
    db_path: str = "sgos.db"
    db_busy_timeout: int = 5000

    # Upload limits
    max_upload_mb: int = 100

    # SSRF protection
    allowed_url_schemes: set[str] = {"http", "https"}
    blocked_hosts: set[str] = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}
    blocked_prefixes: tuple[str, ...] = (
        "169.254.", "10.", "172.16.", "192.168.", "metadata.google"
    )

    # CORS
    allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # LLM (used by idea_generation, repurpose_engine, viral_analytics)
    llm_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-latest-series-invite-beta-v34"
    llm_timeout: int = 60
    llm_max_retries: int = 5

    # SSH (3090 server for SearXNG / Firecrawl)
    ssh_host: str = "3090-lan"
    searxng_port: int = 8888
    firecrawl_port: int = 3002

    # Ingestion
    ingestion_batch_size: int = 30
    ingestion_timeout: int = 30

    model_config = SettingsConfigDict(
        env_prefix="SGOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
