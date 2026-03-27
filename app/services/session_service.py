"""
로컬 세션 관리 서비스.
- 세션 생성 / 검증 / 삭제
- 만료 세션 주기적 정리 (백그라운드 태스크)
- app_settings.default_session_hours 연동
"""
import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from app.database import get_session, get_setting

logger = logging.getLogger("autoreply.session")

COOKIE_NAME = "session_id"


async def create_session(user_id: str) -> tuple[str, str]:
    """
    세션 생성 → (session_id, csrf_token) 반환.
    유효시간은 app_settings.default_session_hours 기반.
    """
    hours = int(await get_setting("default_session_hours", "8"))
    expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    session_id = str(uuid.uuid4())
    csrf_token = secrets.token_hex(32)

    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO sessions (session_id, user_id, expires_at, csrf_token, created_at)
                VALUES (:sid, :uid, :exp, :csrf, :now)
            """),
            {
                "sid": session_id,
                "uid": user_id,
                "exp": expires_at,
                "csrf": csrf_token,
                "now": datetime.utcnow().isoformat(),
            },
        )
        await session.commit()

    return session_id, csrf_token


async def verify_session(session_id: str) -> dict | None:
    """
    세션 유효성 검증 → 사용자 정보 반환 (만료/없음 시 None).
    """
    if not session_id:
        return None

    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT s.session_id, s.user_id, s.expires_at, s.csrf_token,
                       u.email, u.display_name, u.role, u.is_active
                FROM sessions s
                JOIN users u ON s.user_id = u.user_id
                WHERE s.session_id = :sid
            """),
            {"sid": session_id},
        )
        row = result.mappings().first()

    if not row:
        return None

    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        await delete_session(session_id)
        return None

    if not row["is_active"]:
        return None

    return dict(row)


async def delete_session(session_id: str):
    """세션 삭제 (로그아웃)."""
    async with get_session() as session:
        await session.execute(
            text("DELETE FROM sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )
        await session.commit()


async def cleanup_expired_sessions():
    """백그라운드 태스크: 1시간마다 만료된 세션 삭제."""
    while True:
        await asyncio.sleep(3600)
        try:
            async with get_session() as session:
                result = await session.execute(
                    text("DELETE FROM sessions WHERE expires_at < :now"),
                    {"now": datetime.utcnow().isoformat()},
                )
                await session.commit()
                if result.rowcount:
                    logger.info("Cleaned up %d expired sessions", result.rowcount)
        except Exception as exc:
            logger.error("Session cleanup error: %s", exc)
