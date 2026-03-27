"""add projects, project_members, comments, mentions, user_settings

Revision ID: 002
Revises: 001
Create Date: 2026-03-25

Phase 1.5c: 프로젝트 + 팀 기능 테이블 추가
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 신규 테이블 ────────────────────────────────────────────────────────────

    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            project_id   TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            description  TEXT,
            mailing_list TEXT,
            created_by   TEXT REFERENCES users(user_id),
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
            user_id    TEXT REFERENCES users(user_id) ON DELETE CASCADE,
            role       TEXT DEFAULT 'member',
            joined_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (project_id, user_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            project_id      TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
            user_id         TEXT REFERENCES users(user_id),
            content         TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS mentions (
            id                TEXT PRIMARY KEY,
            comment_id        TEXT REFERENCES comments(id) ON DELETE CASCADE,
            mentioned_user_id TEXT REFERENCES users(user_id),
            is_read           INTEGER DEFAULT 0,
            notified_slack    INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT (datetime('now'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id           TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            slack_webhook_url TEXT,
            notify_mention    INTEGER DEFAULT 1,
            notify_new_mail   INTEGER DEFAULT 0,
            notify_claim      INTEGER DEFAULT 0
        )
    """)

    # ── 인덱스 ────────────────────────────────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_comments_conversation ON comments(conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mentions_user ON mentions(mentioned_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id)")

    # ── threads 테이블 컬럼 추가 (기존 DB 호환) ───────────────────────────────
    for col_sql in [
        "ALTER TABLE threads ADD COLUMN user_id TEXT REFERENCES users(user_id)",
        "ALTER TABLE threads ADD COLUMN project_id TEXT REFERENCES projects(project_id)",
        "ALTER TABLE threads ADD COLUMN claimed_by TEXT REFERENCES users(user_id)",
    ]:
        try:
            op.execute(col_sql)
        except Exception:
            pass  # 이미 존재하는 컬럼이면 무시

    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_project ON threads(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_threads_claimed ON threads(claimed_by)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mentions")
    op.execute("DROP TABLE IF EXISTS comments")
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP TABLE IF EXISTS user_settings")
    op.execute("DROP TABLE IF EXISTS projects")
