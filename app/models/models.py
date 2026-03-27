"""
SQLModel table definitions for all DB tables.
- New tables (Phase 1.5a): users, sessions, outlook_tokens, app_settings
- New tables (Phase 1.5c): projects, project_members, comments, mentions, user_settings
- Existing tables: threads, messages, sync_folders, sync_state, auth_tokens
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


# ── 기존 테이블 (Phase 1.5a: 스키마 정의만, 데이터 유지) ──────────────────────────

class AuthToken(SQLModel, table=True):
    __tablename__ = "auth_tokens"
    id: int = Field(default=1, primary_key=True)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[str] = None


class Thread(SQLModel, table=True):
    __tablename__ = "threads"
    conversation_id: str = Field(primary_key=True)
    subject: Optional[str] = None
    status: str = Field(default="inbox")
    latest_at: Optional[str] = None
    has_new_reply: int = Field(default=0)
    created_at: Optional[str] = None
    # Phase 1.5c 추가 컬럼
    user_id: Optional[str] = Field(default=None, foreign_key="users.user_id")
    project_id: Optional[str] = Field(default=None, foreign_key="projects.project_id")
    claimed_by: Optional[str] = Field(default=None, foreign_key="users.user_id")


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    id: str = Field(primary_key=True)
    conversation_id: str
    folder_id: Optional[str] = None
    folder_name: Optional[str] = None
    sender: Optional[str] = None
    sender_email: Optional[str] = None
    to_recipients: Optional[str] = Field(default="[]")
    cc_recipients: Optional[str] = Field(default="[]")
    received_at: Optional[str] = None
    body_preview: Optional[str] = None
    body: Optional[str] = None
    is_read: int = Field(default=0)
    has_attachments: int = Field(default=0)
    is_from_me: int = Field(default=0)
    created_at: Optional[str] = None


class SyncFolder(SQLModel, table=True):
    __tablename__ = "sync_folders"
    folder_id: str = Field(primary_key=True)
    folder_name: Optional[str] = None
    parent_id: Optional[str] = None
    is_synced: int = Field(default=0)
    is_team_visible: int = Field(default=0)
    mail_count: int = Field(default=0)


class SyncState(SQLModel, table=True):
    __tablename__ = "sync_state"
    folder_id: str = Field(primary_key=True)
    delta_link: Optional[str] = None
    last_sync: Optional[str] = None


# ── 신규 테이블 (Phase 1.5a) ──────────────────────────────────────────────────

class User(SQLModel, table=True):
    __tablename__ = "users"
    user_id: str = Field(primary_key=True)
    email: str = Field(index=True, unique=True)
    display_name: str
    password_hash: str
    role: str = Field(default="user")    # "admin" | "user"
    is_active: int = Field(default=1)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Session(SQLModel, table=True):
    __tablename__ = "sessions"
    session_id: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="users.user_id")
    expires_at: str
    csrf_token: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class OutlookToken(SQLModel, table=True):
    __tablename__ = "outlook_tokens"
    user_id: str = Field(primary_key=True, foreign_key="users.user_id")
    ms_user_id: Optional[str] = None
    ms_email: Optional[str] = None
    access_token: Optional[str] = None   # AES-256-GCM encrypted
    refresh_token: Optional[str] = None  # AES-256-GCM encrypted
    expires_at: Optional[str] = None
    connected_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: Optional[str] = None


# ── 신규 테이블 (Phase 1.5c) ──────────────────────────────────────────────────

class Project(SQLModel, table=True):
    __tablename__ = "projects"
    project_id: str = Field(primary_key=True)
    name: str
    description: Optional[str] = None
    mailing_list: Optional[str] = None       # "Office-IT@nexon.co.kr"
    created_by: Optional[str] = Field(default=None, foreign_key="users.user_id")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_members"
    project_id: str = Field(primary_key=True, foreign_key="projects.project_id")
    user_id: str = Field(primary_key=True, foreign_key="users.user_id")
    role: str = Field(default="member")      # "owner" | "member"
    joined_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Comment(SQLModel, table=True):
    __tablename__ = "comments"
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    project_id: Optional[str] = Field(default=None, foreign_key="projects.project_id")
    user_id: str = Field(foreign_key="users.user_id")
    content: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Mention(SQLModel, table=True):
    __tablename__ = "mentions"
    id: str = Field(primary_key=True)
    comment_id: str = Field(foreign_key="comments.id")
    mentioned_user_id: str = Field(foreign_key="users.user_id")
    is_read: int = Field(default=0)
    notified_slack: int = Field(default=0)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class UserSetting(SQLModel, table=True):
    __tablename__ = "user_settings"
    user_id: str = Field(primary_key=True, foreign_key="users.user_id")
    slack_webhook_url: Optional[str] = None  # AES-256-GCM 암호화
    notify_mention: int = Field(default=1)
    notify_new_mail: int = Field(default=0)
    notify_claim: int = Field(default=0)
