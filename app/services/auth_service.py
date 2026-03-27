"""
Microsoft Graph API 토큰 관리 서비스.
Phase 1.5a: auth_tokens 테이블 사용 (단일 사용자 호환성 유지).
Phase 1.5b: outlook_tokens 테이블로 마이그레이션.
"""
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import get_session

logger = logging.getLogger("autoreply.auth_service")


async def get_stored_token() -> dict | None:
    async with get_session() as session:
        result = await session.execute(
            text("SELECT access_token, refresh_token, expires_at FROM auth_tokens WHERE id = 1")
        )
        row = result.mappings().first()
    if not row:
        return None
    return dict(row)


async def save_token(access_token: str, refresh_token: str, expires_in: int):
    expires_at = (datetime.utcnow() + timedelta(seconds=expires_in - 60)).isoformat()
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO auth_tokens (id, access_token, refresh_token, expires_at)
                VALUES (1, :at, :rt, :exp)
                ON CONFLICT(id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at
            """),
            {"at": access_token, "rt": refresh_token, "exp": expires_at},
        )
        await session.commit()


async def refresh_access_token(refresh_token: str) -> str | None:
    # 기본 스코프로 먼저 시도, 실패 시 minimal 스코프로 재시도
    scopes_to_try = [
        settings.scope,
        "offline_access Mail.Read Mail.Read.Shared User.Read",
        "offline_access Mail.Read User.Read",
    ]

    for scope in scopes_to_try:
        payload: dict = {
            "client_id": settings.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": scope,
        }
        if settings.client_secret:
            payload["client_secret"] = settings.client_secret

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(settings.token_url, data=payload)
            if resp.status_code == 200:
                data = resp.json()
                await save_token(
                    data["access_token"],
                    data.get("refresh_token", refresh_token),
                    data.get("expires_in", 3600),
                )
                logger.info("Token refresh succeeded with scope: %s", scope)
                return data["access_token"]
            else:
                err = resp.json()
                logger.warning(
                    "Token refresh failed (scope=%s): %s - %s",
                    scope,
                    err.get("error"),
                    err.get("error_description", "")[:120],
                )

    logger.error("Token refresh failed with all scope fallbacks")
    return None


async def get_valid_access_token() -> str | None:
    token = await get_stored_token()
    if not token:
        return None

    expires_at = datetime.fromisoformat(token["expires_at"])
    if datetime.utcnow() >= expires_at:
        return await refresh_access_token(token["refresh_token"])

    return token["access_token"]


async def exchange_code_for_token(code: str) -> dict:
    payload: dict = {
        "client_id": settings.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "scope": settings.scope,
    }
    if settings.client_secret:
        payload["client_secret"] = settings.client_secret

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.token_url, data=payload)
        data = resp.json()
        if resp.status_code != 200:
            raise Exception(f"Token exchange failed: {data}")

        await save_token(
            data["access_token"],
            data["refresh_token"],
            data.get("expires_in", 3600),
        )
        return data


def build_auth_url(state: str = "login") -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": settings.client_id,
        "response_type": "code",
        "redirect_uri": settings.redirect_uri,
        "response_mode": "query",
        "scope": settings.scope,
        "state": state,
    }
    return f"{settings.auth_url}?{urlencode(params)}"
