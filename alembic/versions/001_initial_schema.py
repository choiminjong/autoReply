"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-25

기존 테이블 + 신규 테이블 (users, sessions, outlook_tokens, app_settings).
기존 DB 보호를 위해 모든 CREATE TABLE에 IF NOT EXISTS 사용.
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 기존 테이블 (보존) ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            conversation_id TEXT PRIMARY KEY,
            subject TEXT,
            status TEXT DEFAULT 'inbox',
            latest_at TEXT,
            has_new_reply INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            folder_id TEXT,
            folder_name TEXT,
            sender TEXT,
            sender_email TEXT,
            to_recipients TEXT DEFAULT '[]',
            cc_recipients TEXT DEFAULT '[]',
            received_at TEXT,
            body_preview TEXT,
            body TEXT,
            is_read INTEGER DEFAULT 0,
            has_attachments INTEGER DEFAULT 0,
            is_from_me INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS sync_folders (
            folder_id TEXT PRIMARY KEY,
            folder_name TEXT,
            parent_id TEXT,
            is_synced INTEGER DEFAULT 0,
            is_team_visible INTEGER DEFAULT 0,
            mail_count INTEGER DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            folder_id TEXT PRIMARY KEY,
            delta_link TEXT,
            last_sync TEXT
        )
    """)

    # ── 신규 테이블 (Phase 1.5a) ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT REFERENCES users(user_id),
            expires_at TEXT NOT NULL,
            csrf_token TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS outlook_tokens (
            user_id TEXT PRIMARY KEY REFERENCES users(user_id),
            ms_user_id TEXT,
            ms_email TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            connected_at TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── 인덱스 ─────────────────────────────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_latest ON threads(latest_at DESC)")

    # ── sync_folders에 is_team_visible 컬럼 추가 (기존 DB 호환) ───────────
    # SQLite는 IF NOT EXISTS를 ADD COLUMN에서 지원하지 않으므로 예외 처리
    try:
        op.execute("ALTER TABLE sync_folders ADD COLUMN is_team_visible INTEGER DEFAULT 0")
    except Exception:
        pass  # 이미 존재하면 무시


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
    op.execute("DROP TABLE IF EXISTS outlook_tokens")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")
