"""관리 명령.

    python -m app.cli init-db              # 마이그레이션 적용 + 초기 관리자 생성
    python -m app.cli seed-sample [--seed 42] [--reset]
    python -m app.cli serve
"""

from __future__ import annotations

import argparse

from alembic import command
from alembic.config import Config

from app.config import BACKEND_DIR, get_settings


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    if url:
        cfg.attributes["url"] = url
    return cfg


def init_db(url: str | None = None) -> None:
    command.upgrade(alembic_config(url), "head")
    from app.auth.provider import ensure_initial_admin
    from app.db import SessionLocal

    s = get_settings()
    with SessionLocal() as db:
        created = ensure_initial_admin(db, s.auth_initial_admin_username, s.auth_initial_admin_password)
    print("DB 마이그레이션 완료.")
    if created:
        print(f"초기 관리자 계정 생성: {s.auth_initial_admin_username} (비밀번호는 설정 파일 값, 로그인 후 바로 변경하세요)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="DB 마이그레이션 적용 + 초기 관리자 생성")
    seed = sub.add_parser("seed-sample", help="샘플 데이터 생성")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--reset", action="store_true", help="기존 트랜잭션/마스터 데이터를 지우고 생성")
    sub.add_parser("serve", help="서버 실행")
    args = parser.parse_args(argv)

    if args.cmd == "init-db":
        init_db()
    elif args.cmd == "seed-sample":
        from app.db import SessionLocal
        from app.sample.generator import generate

        from app.services.forecast_service import run_all

        with SessionLocal() as db:
            scenario = generate(db, seed=args.seed, reset=args.reset)
            print("ARIMA 예측 실행 중…")
            run_all(db)
        print("샘플 데이터 생성 완료.")
        for key, value in scenario.summary().items():
            print(f"  {key}: {value}")
    elif args.cmd == "serve":
        import uvicorn

        s = get_settings()
        uvicorn.run("app.main:app", host=s.server_host, port=s.server_port, workers=1)


if __name__ == "__main__":
    main()

