"""
인증 미들웨어 + FastAPI Depends 헬퍼.
- /api/auth/* → 세션 체크 없이 통과 (/api/auth/callback 포함)
- /api/outlook/connect → 세션 체크 없이 통과 (OAuth 시작)
- /api/* → 세션 쿠키 검증 필수
- POST/PATCH/DELETE → X-CSRF-Token 헤더 검증
"""
import logging
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.session_service import COOKIE_NAME, verify_session

logger = logging.getLogger("autoreply.middleware")

# 인증 없이 통과할 경로 prefix
_PUBLIC_PREFIXES = (
    "/api/auth/",        # /api/auth/callback도 자동 포함
    "/api/outlook/connect",
)
_PUBLIC_EXACT = {"/api/auth/login", "/api/auth/register"}

# CSRF 검증 제외 경로 (GET/HEAD/OPTIONS는 자동 제외)
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 정적 파일 / 루트 / 비API 경로는 통과
        if not path.startswith("/api/"):
            return await call_next(request)

        # 공개 API 경로 통과
        for prefix in _PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # 세션 검증
        session_id = request.cookies.get(COOKIE_NAME)
        user = await verify_session(session_id)

        if not user:
            return JSONResponse(
                status_code=401,
                content={"detail": "로그인이 필요합니다."},
            )

        # CSRF 검증 (상태 변경 요청)
        if request.method not in _CSRF_SAFE_METHODS:
            csrf_token = request.headers.get("X-CSRF-Token")
            if csrf_token != user.get("csrf_token"):
                logger.warning("CSRF validation failed for %s %s", request.method, path)
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 토큰 검증 실패"},
                )

        request.state.user = user
        return await call_next(request)


# ── FastAPI Depends 헬퍼 ──────────────────────────────────────────────────────

async def require_login(request: Request) -> dict:
    """로그인된 사용자 정보를 반환. 미로그인 시 401."""
    session_id = request.cookies.get(COOKIE_NAME)
    user = await verify_session(session_id)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


async def require_admin(request: Request) -> dict:
    """admin 역할 사용자만 허용. 미로그인/권한 없음 시 401/403."""
    user = await require_login(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
