#!/bin/sh
# 컨테이너 시작: DB 마이그레이션 → (처음이고 BIF_DEMO 가 있으면) 데모 데이터 → 서버
set -e
cd /app/backend
python -m app.cli init-db
if [ -n "$BIF_DEMO" ] && [ ! -f /app/data/.demo_loaded ]; then
  case "$BIF_DEMO" in
    injection) python -m app.cli seed-process-log --reset --file /app/samples/injection_m3/hourly_2026-03-04_06.csv --mapping /app/samples/injection_m3/mapping.json ;;
    sample)    python -m app.cli seed-sample --reset ;;
  esac
  touch /app/data/.demo_loaded
fi
exec python -m app.cli serve
