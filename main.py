import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import bcrypt
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import get_session, init_db
from app.middleware import AuthMiddleware
from app.routers import auth, folders, sync, threads
from app.routers.threads import attachments_router
from app.routers import outlook as outlook_router
from app.routers import admin as admin_router
from app.routers import projects as projects_router
from app.routers import comments as comments_router
from app.services.poller import start_poller
from app.services.session_service import COOKIE_NAME, cleanup_expired_sessions, verify_session
from app.services.websocket_manager import ws_manager

DEFAULT_ADMIN_EMAIL = "admin@nexon.co.kr"
DEFAULT_ADMIN_PASSWORD = "Nexon!1234"
DEFAULT_ADMIN_NAME = "관리자"


async def ensure_default_admin():
    """admin 계정이 하나도 없으면 기본 admin 계정을 생성한다."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT COUNT(*) as cnt FROM users WHERE role='admin'")
        )
        cnt = result.mappings().first()["cnt"]

    if cnt > 0:
        return

    password_hash = bcrypt.hashpw(
        DEFAULT_ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    async with get_session() as session:
        await session.execute(
            text("""
                INSERT OR IGNORE INTO users
                    (user_id, email, display_name, password_hash, role, is_active, created_at)
                VALUES (:uid, :email, :name, :hash, 'admin', 1, :now)
            """),
            {
                "uid": str(uuid.uuid4()),
                "email": DEFAULT_ADMIN_EMAIL,
                "name": DEFAULT_ADMIN_NAME,
                "hash": password_hash,
                "now": datetime.utcnow().isoformat(),
            },
        )
        await session.commit()

    logger.info("기본 admin 계정 생성됨: %s", DEFAULT_ADMIN_EMAIL)

# ── 구조화 로깅 설정 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autoreply")


# ── 앱 생명주기 ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data", exist_ok=True)
    os.makedirs("docs/build", exist_ok=True)
    os.makedirs("docs/test", exist_ok=True)
    os.makedirs("docs/plan", exist_ok=True)
    os.makedirs("docs/release", exist_ok=True)

    await init_db()
    logger.info("Database initialized")

    await ensure_default_admin()

    # 백그라운드 태스크 시작
    poller_task = asyncio.create_task(start_poller())
    cleanup_task = asyncio.create_task(cleanup_expired_sessions())

    yield

    poller_task.cancel()
    cleanup_task.cancel()
    try:
        await poller_task
    except asyncio.CancelledError:
        pass
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


# ── FastAPI 앱 ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Mail Kanban — Team AutoReply",
    version="1.5.0",
    lifespan=lifespan,
)

# 인증 미들웨어
app.add_middleware(AuthMiddleware)


# ── 액세스 로그 미들웨어 (디버그용) ─────────────────────────────────────────────

@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    import time
    start = time.time()
    method = request.method
    path = request.url.path
    query = str(request.url.query)

    # OAuth 관련 요청은 더 상세하게 출력
    is_oauth = "/connect" in path or "/callback" in path
    if is_oauth:
        print(f"\n{'='*60}")
        print(f"[ACCESS] ▶ {method} {path}")
        if query:
            print(f"[ACCESS]   query: {query}")
        print(f"[ACCESS]   headers: {dict(request.headers)}")
    else:
        print(f"[ACCESS] {method} {path}" + (f"?{query}" if query else ""))

    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000)

    if is_oauth:
        print(f"[ACCESS] ◀ {method} {path} → {response.status_code} ({elapsed}ms)")
        print(f"{'='*60}\n")
    else:
        print(f"[ACCESS] {method} {path} → {response.status_code} ({elapsed}ms)")

    return response

# ── 전역 에러 핸들러 ──────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "서버 내부 오류가 발생했습니다."})


# ── API 라우터 ─────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(outlook_router.router)
app.include_router(folders.router)
app.include_router(threads.router)
app.include_router(attachments_router)
app.include_router(sync.router)
app.include_router(admin_router.router)
app.include_router(projects_router.router)
app.include_router(comments_router.router)


# ── WebSocket (인증 포함) ──────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session_id = websocket.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user:
        await websocket.close(code=4001)
        return

    user_id = user["user_id"]
    await ws_manager.connect(websocket, user_id=user_id)
    try:
        while True:
            # 클라이언트에서 프로젝트 선택 메시지 수신
            raw = await websocket.receive_text()
            try:
                import json
                msg = json.loads(raw)
                if msg.get("type") == "set_project":
                    ws_manager.set_project(websocket, msg.get("project_id", ""))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── 정적 파일 + 페이지 라우팅 ──────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root(request: Request):
    """루트 → 로그인 상태에 따라 리다이렉트."""
    session_id = request.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")
    return FileResponse("static/index.html")


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.get("/register")
async def register_page():
    return FileResponse("static/register.html")


@app.get("/settings")
async def settings_page(request: Request):
    session_id = request.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")
    return FileResponse("static/settings.html")


@app.get("/admin")
async def admin_page(request: Request):
    session_id = request.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user or user.get("role") != "admin":
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")
    return FileResponse("static/admin.html")


@app.get("/projects")
async def projects_page(request: Request):
    session_id = request.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")
    return FileResponse("static/projects.html")


if __name__ == "__main__":
    import uvicorn
    from app.config import settings
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
