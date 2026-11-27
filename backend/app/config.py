"""서버 설정.

우선순위: 환경변수(BIF_*) > 설정 파일(BIF_CONFIG 경로, 기본 config/config.toml) > 기본값.
업무 설정(넬슨룰 기본값, 가중치, ARIMA 파라미터 등)은 DB Setting 테이블에서 관리하며
기본값은 app.core.defaults 에 있다.
"""

from __future__ import annotations

import logging
import os
import secrets
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
    # secret_key 가 공개된 기본값이면 이 파일에 임의 키를 만들어 쓴다 (get_secret_key 참고)
    auth_secret_file: str = str(PROJECT_DIR / "data" / "secret.key")
    # 저장소에 공개된 초기 비밀번호(admin1234 등)를 그대로 쓰게 둘지. 기본은 첫 로그인 때 변경 강제
    auth_allow_default_passwords: bool = False
    auth_token_ttl_minutes: int = 720
    auth_initial_admin_username: str = "admin"
    auth_initial_admin_password: str = "admin1234"

    # [llm]
    llm_ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = 0.1
    llm_num_ctx: int = 8192
    llm_timeout_seconds: float = 300.0  # 로컬 대형 모델은 답변에 수 분 걸릴 수 있음

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


# 저장소(코드, 설정 예시)에 공개된 값. 이 값으로 서명하면 누구나 접속 토큰을 만들 수 있다.
INSECURE_SECRET_KEYS = frozenset({"", "change-me-in-config", "반드시-긴-임의-문자열로-바꾸세요"})
_MIN_GENERATED_KEY_CHARS = 32


def _load_or_create_secret(path: Path) -> str:
    """파일에 저장된 임의 키를 읽고, 없으면 만들어 저장한다 (소유자만 읽기)."""
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= _MIN_GENERATED_KEY_CHARS:
            return key
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # 다른 프로세스가 먼저 만들었다
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    return key


@lru_cache
def get_secret_key() -> str:
    """접속 토큰 서명 키. 설정값이 공개된 기본값이면 서버마다 임의 키를 만들어 data/secret.key 에 두고 쓴다."""
    s = get_settings()
    if s.auth_secret_key not in INSECURE_SECRET_KEYS:
        return s.auth_secret_key
    key = _load_or_create_secret(Path(s.auth_secret_file))
    logging.getLogger("app.config").warning(
        "secret_key 가 공개된 기본값이라 임의 키를 사용합니다: %s", s.auth_secret_file
    )
    return key
