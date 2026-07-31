from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTAGES_DIR = DATA_DIR / "outages"
DB_PATH = DATA_DIR / "agent.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "NetGuard Voice Agent"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    database_url: str = f"sqlite+aiosqlite:///{DB_PATH}"
    outages_dir: Path = OUTAGES_DIR
    max_rag_chunks: int = 5
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: float = 30.0
    request_timeout_seconds: float = 30.0
    stt_retry_attempts: int = 3
    llm_retry_attempts: int = 3
    tts_retry_attempts: int = 3


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTAGES_DIR.mkdir(parents=True, exist_ok=True)
