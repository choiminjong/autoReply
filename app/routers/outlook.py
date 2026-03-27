"""
Outlook 커넥터 — Microsoft OAuth2 연동.
- /api/outlook/connect    → Microsoft OAuth 시작
- /api/outlook/status     → 연동 상태 조회
- /api/outlook/disconnect → 연동 해제
OAuth 콜백은 /api/auth/callback (app/routers/auth.py) 에서 처리.
"""
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.config import settings
from app.database import get_session
from app.middleware import require_login
from app.routers.admin import get_active_scope

logger = logging.getLogger("autoreply.outlook")

router = APIRouter(prefix="/api/outlook", tags=["outlook"])

# ── OAuth ─────────────────────────────────────────────────────────────────────

@router.get("/connect")
async def connect(user: dict = Depends(require_login)):
    """Microsoft OAuth 시작 — 로그인 필요."""
    active_scope = await get_active_scope()
    params = {
        "client_id": settings.client_id,
        "response_type": "code",
        "redirect_uri": settings.redirect_uri,
        "response_mode": "query",
        "scope": active_scope,
        "state": user["user_id"],
    }
    url = f"{settings.auth_url}?{urlencode(params)}"

    print("=" * 60)
    print("[OAUTH] /api/outlook/connect 호출됨")
    print(f"[OAUTH] user_id   : {user['user_id']}")
    print(f"[OAUTH] client_id : {settings.client_id}")
    print(f"[OAUTH] tenant_id : {settings.tenant_id}")
    print(f"[OAUTH] redirect  : {settings.redirect_uri}")
    print(f"[OAUTH] scope     : {active_scope}")
    print(f"[OAUTH] auth_url  : {settings.auth_url}")
    print(f"[OAUTH] 최종 URL  : {url}")
    print("=" * 60)

    return RedirectResponse(url=url)


@router.get("/status")
async def status(user: dict = Depends(require_login)):
    """Outlook 연동 상태 조회."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT ms_email, expires_at, connected_at FROM outlook_tokens WHERE user_id = :uid"),
            {"uid": user["user_id"]},
        )
        row = result.mappings().first()

    if not row:
        return {"connected": False}

    return {
        "connected": True,
        "ms_email": row["ms_email"],
        "expires_at": row["expires_at"],
        "connected_at": row["connected_at"],
    }


@router.delete("/disconnect")
async def disconnect(user: dict = Depends(require_login)):
    """Outlook 연동 해제 — 토큰 삭제."""
    async with get_session() as session:
        await session.execute(
            text("DELETE FROM outlook_tokens WHERE user_id = :uid"),
            {"uid": user["user_id"]},
        )
        await session.commit()

    logger.info("Outlook disconnected for user_id=%s", user["user_id"])
    return {"message": "Outlook 연동이 해제되었습니다."}
