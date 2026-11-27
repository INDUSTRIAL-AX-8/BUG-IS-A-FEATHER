#!/usr/bin/env bash
# 인터넷 되는 PC(Linux/macOS)에서 실행: 폐쇄망 서버로 옮길 설치 묶음을 만든다.
#   bash scripts/bundle_offline.sh [모델이름]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/offline_bundle"
MODEL="${1:-}"
rm -rf "$OUT" && mkdir -p "$OUT/wheels"

echo "[1/4] 소스 복사"; (cd "$ROOT" && git archive --format=tar.gz -o "$OUT/app.tar.gz" HEAD)
echo "[2/4] 프론트엔드 빌드"; (cd "$ROOT/frontend" && npm ci && npm run build) && cp -r "$ROOT/frontend/dist" "$OUT/frontend_dist"
echo "[3/4] Python wheel"; python3 -m pip download -r "$ROOT/backend/requirements.lock" -d "$OUT/wheels" \
  --only-binary=:all: --platform manylinux2014_x86_64 --python-version "${PYTHON_VERSION:-3.11}"
echo "[4/4] Ollama 모델"
if [ -n "$MODEL" ]; then ollama pull "$MODEL" && cp -r "$HOME/.ollama/models" "$OUT/ollama_models"; fi
tar -czf "$ROOT/offline_bundle.tar.gz" -C "$OUT" .
echo "완료: $ROOT/offline_bundle.tar.gz"
