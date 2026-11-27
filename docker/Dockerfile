# 선택 사항: Docker 로 실행할 때만 사용 (기본 설치는 docs/OFFLINE_INSTALL.md, Docker 불필요)
#   docker build -f docker/Dockerfile -t bif .
#   docker run -p 8000:8000 -v bif-data:/app/data -e BIF_LLM_OLLAMA_URL=http://host.docker.internal:11434 bif
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.lock backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend/ backend/
COPY config/ config/
COPY --from=web /web/dist frontend/dist
ENV BIF_SERVER_HOST=0.0.0.0 BIF_SERVER_PORT=8000 PYTHONUTF8=1
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "python -m app.cli init-db && python -m app.cli serve"]
