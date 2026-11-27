"""FastAPI 앱. 서버 하나로 API 와 프론트 빌드 산출물을 함께 서빙한다 (외부 리소스 없음)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, lots, master
from app.config import get_settings
from app.errors import install_handlers

API_PREFIX = "/api"


def _routers() -> list[APIRouter]:
    routers: list[APIRouter] = [auth.router, lots.router, *master.routers]
    # 이후 Phase 의 라우터는 존재하면 등록
    for module_name in (
        "app.api.spc",
        "app.api.causes",
        "app.api.defects",
        "app.api.forecast",
        "app.api.assistant",
        "app.api.ingest",
        "app.api.events",
        "app.api.accuracy",
        "app.api.analysis",
        "app.api.live",
    ):
        try:
            module = __import__(module_name, fromlist=["router"])
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        routers.append(module.router)
    return routers


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if app.state.start_scheduler:
        try:
            from app.jobs.scheduler import start_scheduler
        except ModuleNotFoundError:
            start_scheduler = None
        if start_scheduler:
            scheduler = start_scheduler()
    yield
    try:
        from app.live.simulator import get_simulator

        get_simulator().stop()
    except Exception:
        pass
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app(start_scheduler: bool | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="계보 기반 불량 원인 추적",
        lifespan=lifespan,
        # 문서 화면(Swagger)은 CDN 을 쓰므로 폐쇄망에서는 끈다. 스키마 JSON 은 /api/openapi.json
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.start_scheduler = (
        settings.scheduler_enabled if start_scheduler is None else start_scheduler
    )
    install_handlers(app)
    for r in _routers():
        app.include_router(r, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", tags=["상태"])
    def health():
        return {"status": "ok"}

    _mount_frontend(app, Path(settings.server_frontend_dist))
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def _no_frontend():
            return JSONResponse(
                {"message": "프론트엔드 빌드 산출물이 없습니다. frontend 에서 npm run build 를 실행하세요."}
            )

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def _spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"message": "없는 API 입니다."}, status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        if "." in path.rsplit("/", 1)[-1]:
            # 확장자가 있는 없는 파일(예: 다른 앱이 남긴 /sw.js)은 404. index.html 을 돌려주면
            # 같은 주소(localhost:포트)를 쓰던 다른 앱의 서비스 워커가 갱신에 실패한 채 계속 살아남는다.
            return JSONResponse({"message": "없는 파일입니다."}, status_code=404)
        # index.html 은 캐시하지 않는다 (업데이트 후 옛 번들이 남지 않게). 해시가 붙은 assets 는 캐시 가능
        return FileResponse(index, headers={"Cache-Control": "no-cache"})  # SPA 라우팅


app = create_app()
