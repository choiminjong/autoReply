"""
SQLModel + SQLAlchemy async 데이터베이스 설정.
- create_async_engine: SQLite (aiosqlite 드라이버)
- AUTO_MIGRATE=true 시 서비스 시작 시 alembic upgrade head 자동 실행
- AUTO_MIGRATE=false 시 수동 (`alembic upgrade head --sql > migration.sql`)
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.config import settings

# 모델 임포트 — SQLModel.metadata에 테이블 등록
import app.models.models  # noqa: F401

logger = logging.getLogger("autoreply.db")

engine = None
AsyncSessionLocal = None


def _setup_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def init_db():
    global engine, AsyncSessionLocal

    engine = create_async_engine(
        settings.database_url,
        echo=False,
    )

    # SQLite PRAGMA 설정
    event.listen(engine.sync_engine, "connect", _setup_sqlite_pragmas)

    AsyncSessionLocal = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    if settings.auto_migrate:
        await _run_migrations()
    else:
        logger.info("AUTO_MIGRATE=false — skipping automatic migration")

    await _seed_app_settings()
    logger.info("Database initialized (url=%s)", settings.database_url)


async def _run_migrations():
    """
    AUTO_MIGRATE=true 시 alembic upgrade head 자동 실행.
    실패해도 서비스는 기동됨 (fallback: SQLModel create_all).
    """
    try:
        import asyncio
        from alembic.config import Config as AlembicConfig
        from alembic import command

        def _upgrade():
            cfg = AlembicConfig("alembic.ini")
            command.upgrade(cfg, "head")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _upgrade)
        logger.info("Alembic migration completed (head)")
    except Exception as exc:
        logger.warning("Alembic migration failed (%s) — falling back to SQLModel create_all", exc)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)


async def _seed_app_settings():
    """서비스 최초 실행 시 app_settings 기본값 삽입."""
    defaults = {
        "default_session_hours": "8",
        "max_session_hours": "24",
        "team_slack_webhook": "",
        "unclaimed_alert_minutes": "30",
    }
    async with get_session() as session:
        for key, value in defaults.items():
            await session.execute(
                text("INSERT OR IGNORE INTO app_settings (key, value) VALUES (:k, :v)"),
                {"k": key, "v": value},
            )
        await session.commit()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """비동기 DB 세션 컨텍스트 매니저."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_session_dep() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends용 세션 제너레이터."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_setting(key: str, default: str = "") -> str:
    """app_settings에서 설정값 조회."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT value FROM app_settings WHERE key = :k"),
            {"k": key},
        )
        row = result.mappings().first()
        return row["value"] if row else default
