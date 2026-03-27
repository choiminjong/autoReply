"""
백그라운드 Delta Sync 폴러 — 멀티유저 지원.
- 모든 outlook_tokens 연동 사용자를 순회하며 delta sync
- Rate Limit 대응: 429 응답 시 지수 백오프 + Retry-After 헤더 준수
- 토큰 만료 시 자동 refresh (outlook_tokens 기반)
- 사용자별 폴링 간격 분산 (전원 동시 시작 방지)
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import get_session
from app.services.crypto import decrypt, get_key
from app.services.outlook import delta_sync_folder
from app.services.websocket_manager import ws_manager

logger = logging.getLogger("autoreply.poller")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ─── Rate Limit 대응 ───────────────────────────────────────────────────────────

async def _call_graph_with_backoff(client: httpx.AsyncClient, url: str, headers: dict, retries: int = 3) -> dict | None:
    """Rate Limit 대응: 429 시 Retry-After 헤더 준수 + 지수 백오프."""
    for attempt in range(retries):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30 * (2 ** attempt)))
                logger.warning("Rate limited (attempt %d/%d), waiting %ds", attempt + 1, retries, retry_after)
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Graph API returned %d for %s", resp.status_code, url)
            return None
        except Exception as e:
            wait = 5 * (2 ** attempt)
            logger.error("Graph API call failed (attempt %d): %s — retry in %ds", attempt + 1, e, wait)
            await asyncio.sleep(wait)
    logger.error("Graph API rate limit exceeded after %d retries", retries)
    return None


# ─── 토큰 관리 ────────────────────────────────────────────────────────────────

async def _refresh_outlook_token(user_id: str, refresh_token_enc: str) -> str | None:
    """outlook_tokens 기반 토큰 갱신. 성공 시 새 access_token 반환."""
    key = get_key()
    refresh_token = decrypt(refresh_token_enc, key)
    if not refresh_token:
        return None

    payload: dict = {
        "client_id": settings.client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": settings.scope,
    }
    if settings.client_secret:
        payload["client_secret"] = settings.client_secret

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.token_url, data=payload)
        if resp.status_code != 200:
            logger.warning("Token refresh failed for user %s: %s", user_id, resp.text)
            return None
        data = resp.json()

    new_access = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)
    expires_at = (datetime.utcnow() + timedelta(seconds=expires_in - 60)).isoformat()

    from app.services.crypto import encrypt
    async with get_session() as session:
        await session.execute(
            text("""
                UPDATE outlook_tokens
                SET access_token=:at, refresh_token=:rt, expires_at=:exp
                WHERE user_id=:uid
            """),
            {
                "at": encrypt(new_access, key),
                "rt": encrypt(new_refresh, key),
                "exp": expires_at,
                "uid": user_id,
            },
        )
        await session.commit()

    logger.info("Token refreshed for user %s", user_id)
    return new_access


async def _get_valid_token_for_user(row: dict) -> str | None:
    """outlook_tokens에서 사용자별 유효 토큰 반환. 만료 시 자동 refresh."""
    key = get_key()

    if not row.get("access_token"):
        return None

    expires_at = row.get("expires_at", "")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= datetime.utcnow():
                logger.info("Token expired for user %s, refreshing...", row["user_id"])
                return await _refresh_outlook_token(row["user_id"], row["refresh_token"])
        except ValueError:
            pass

    return decrypt(row["access_token"], key)


# ─── 단일 사용자 Delta Sync ───────────────────────────────────────────────────

async def _sync_user(user_id: str, token: str, ms_email: str, folders: list):
    """단일 사용자의 동기화 폴더를 순회하며 delta sync."""
    total_new = 0
    for folder in folders:
        try:
            new_msgs = await delta_sync_folder(folder["folder_id"], folder["folder_name"], ms_email)
            total_new += len(new_msgs)
        except Exception as e:
            logger.error("Delta sync error for user %s / folder %s: %s", user_id, folder["folder_name"], e)

    if total_new > 0:
        await ws_manager.broadcast_to_user(user_id, {
            "type": "new_mail",
            "data": {"count": total_new},
        })
        logger.info("User %s: %d new messages", user_id, total_new)


# ─── 멀티유저 폴러 ─────────────────────────────────────────────────────────────

async def run_multiuser_delta_sync():
    """모든 연동 사용자를 순회하며 delta sync 실행."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT user_id, ms_email, access_token, refresh_token, expires_at FROM outlook_tokens")
        )
        users = result.mappings().all()

    if not users:
        return

    for user_row in users:
        user_id = user_row["user_id"]

        token = await _get_valid_token_for_user(dict(user_row))
        if not token:
            logger.debug("No valid token for user %s, skipping", user_id)
            continue

        # 해당 사용자의 동기화 폴더 조회
        async with get_session() as session:
            result = await session.execute(
                text("SELECT folder_id, folder_name FROM sync_folders WHERE is_synced=1")
            )
            folders = result.mappings().all()

        ms_email = user_row.get("ms_email", "")
        await _sync_user(user_id, token, ms_email, folders)

        # 사용자 간 요청 분산 (Rate Limit 방지)
        await asyncio.sleep(random.uniform(1, 3))


async def start_poller():
    """백그라운드 폴러 시작."""
    interval = settings.poll_interval_seconds
    logger.info("Multiuser background poller started (interval=%ds)", interval)

    # 첫 실행을 약간 지연 (서비스 시작 직후 부하 분산)
    await asyncio.sleep(30)

    while True:
        try:
            await run_multiuser_delta_sync()
        except Exception as e:
            logger.error("Poller error: %s", e)
        await asyncio.sleep(interval)
