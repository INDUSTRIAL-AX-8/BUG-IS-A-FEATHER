"""서버 설정.

우선순위: 환경변수(BIF_*) > 설정 파일(BIF_CONFIG 경로, 기본 config/config.toml) > 기본값.
업무 설정(넬슨룰 기본값, 가중치, ARIMA 파라미터 등)은 DB Setting 테이블에서 관리하며
기본값은 app.core.defaults 에 있다.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


def _config_file() -> Path:
    return Path(os.environ.get("BIF_CONFIG", PROJECT_DIR / "config" / "config.toml"))


class _TomlSource(PydanticBaseSettingsSource):
    """config.toml 의 [server], [database], [auth], [llm] 섹션을 평탄화해서 읽는다."""

    def get_field_value(self, field, field_name):  # pragma: no cover - 사용 안 함
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = _config_file()
        if not path.is_file():
            return {}
        with path.open("rb") as f:
            raw = tomllib.load(f)
        flat: dict[str, Any] = {}
        for section, values in raw.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    flat[f"{section}_{key}"] = value
            else:
                flat[section] = values
        return flat


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIF_", extra="ignore")

    # [database]
    database_url: str = f"sqlite:///{(PROJECT_DIR / 'data' / 'app.db').as_posix()}"
    database_echo: bool = False

    # [server]
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    server_frontend_dist: str = str(PROJECT_DIR / "frontend" / "dist")

    # [auth]
    auth_secret_key: str = "change-me-in-config"
    auth_token_ttl_minutes: int = 720
    auth_initial_admin_username: str = "admin"
    auth_initial_admin_password: str = "admin1234"

    # [llm]
    llm_ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = 0.1
    llm_num_ctx: int = 8192
    llm_timeout_seconds: float = 120.0

    # [kamp] KAMP 다이캐스팅 원본 CSV 폴더 (seed-kamp 기본값)
    kamp_raw_dir: str | None = None

    # [scheduler]
    scheduler_enabled: bool = True

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings, env_settings, _TomlSource(settings_cls), file_secret_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
