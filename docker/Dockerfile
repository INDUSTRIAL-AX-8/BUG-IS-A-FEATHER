# 선택 사항: Docker 로 실행할 때만 사용 (기본 설치는 set_up.bat / docs/OFFLINE_INSTALL.md, Docker 불필요)
#   docker build -f docker/Dockerfile -t bug-is-a-feather .
#   docker run -d --name bif -p 8300:8000 -v bif-data:/app/data -e BIF_DEMO=injection bug-is-a-feather
#   → http://localhost:8300   (BIF_DEMO: injection | sample | 비움=빈 DB)
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
COPY backend/requirements.lock backend/requirements.lock
RUN pip install -r backend/requirements.lock
COPY backend/ backend/
COPY config/ config/
COPY samples/ samples/
COPY --from=web /web/dist frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh && mkdir -p /app/data
ENV BIF_SERVER_HOST=0.0.0.0 BIF_SERVER_PORT=8000 \
    BIF_DATABASE_URL=sqlite:////app/data/app.db \
    BIF_LLM_OLLAMA_URL=http://host.docker.internal:11434
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
ENTRYPOINT ["/entrypoint.sh"]
