"""
관리자 전용 API.
- 사용자 목록 조회 / 역할 변경 / 비활성화
- 시스템 설정 조회 / 변경
- Microsoft Graph API 권한(scope) 조회 / 변경
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional, List

from app.config import settings
from app.database import get_session
from app.middleware import require_admin, require_login

logger = logging.getLogger("autoreply.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class SettingUpdateRequest(BaseModel):
    value: str


class ScopeUpdateRequest(BaseModel):
    scopes: List[str]


# 고정 권한 정의
SCOPE_CATALOG = [
    {"scope": "offline_access",     "label": "토큰 갱신",           "required": True},
    {"scope": "Mail.Read",          "label": "메일 읽기",           "required": True},
    {"scope": "Mail.Read.Shared",   "label": "공유 메일함 읽기",    "required": False},
    {"scope": "Mail.ReadWrite",     "label": "메일 이동/상태 변경", "required": False},
    {"scope": "Mail.Send",          "label": "메일 발송",           "required": False},
    {"scope": "User.Read",          "label": "사용자 정보",         "required": True},
    {"scope": "People.Read",        "label": "수신자 자동완성",     "required": False},
]
REQUIRED_SCOPES = {s["scope"] for s in SCOPE_CATALOG if s["required"]}
VALID_SCOPES    = {s["scope"] for s in SCOPE_CATALOG}


async def get_active_scope() -> str:
    """DB에 저장된 scope가 있으면 반환, 없으면 config 기본값."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT value FROM app_settings WHERE key='graph_scopes'")
        )
        row = result.mappings().first()
    return row["value"] if row else settings.scope


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    """사용자 목록 + Outlook 연동 상태."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT u.user_id, u.email, u.display_name, u.role, u.is_active, u.created_at,
                       o.ms_email, o.expires_at as outlook_expires_at, o.connected_at
                FROM users u
                LEFT JOIN outlook_tokens o ON o.user_id = u.user_id
                ORDER BY u.created_at ASC
            """)
        )
        rows = result.mappings().all()
    return {"users": [dict(r) for r in rows]}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdateRequest, admin: dict = Depends(require_admin)):
    """역할 변경 또는 비활성화. 자기 자신 역할 변경 불가."""
    if user_id == admin["user_id"] and body.role is not None:
        raise HTTPException(status_code=400, detail="자기 자신의 역할은 변경할 수 없습니다.")

    # admin 최소 1명 유지
    if body.role == "user":
        async with get_session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) as cnt FROM users WHERE role='admin' AND is_active=1")
            )
            cnt = result.mappings().first()["cnt"]
        if cnt <= 1:
            raise HTTPException(status_code=400, detail="관리자는 최소 1명 이상이어야 합니다.")

    async with get_session() as session:
        if body.role is not None:
            await session.execute(
                text("UPDATE users SET role=:role WHERE user_id=:uid"),
                {"role": body.role, "uid": user_id},
            )
        if body.is_active is not None:
            await session.execute(
                text("UPDATE users SET is_active=:active WHERE user_id=:uid"),
                {"active": 1 if body.is_active else 0, "uid": user_id},
            )
            if not body.is_active:
                # 비활성화 시 세션 삭제
                await session.execute(
                    text("DELETE FROM sessions WHERE user_id=:uid"),
                    {"uid": user_id},
                )
                # 비활성화 시 클레임 해제 (팀 운영 연속성)
                await session.execute(
                    text("UPDATE threads SET claimed_by=NULL WHERE claimed_by=:uid"),
                    {"uid": user_id},
                )
        await session.commit()

    logger.info("Admin %s updated user %s: %s", admin["email"], user_id, body.model_dump(exclude_none=True))
    return {"message": "사용자 정보가 업데이트되었습니다."}


@router.get("/settings")
async def get_settings(admin: dict = Depends(require_admin)):
    """시스템 설정 전체 조회."""
    async with get_session() as session:
        result = await session.execute(text("SELECT key, value FROM app_settings"))
        rows = result.mappings().all()
    return {"settings": {r["key"]: r["value"] for r in rows}}


@router.patch("/settings/{key}")
async def update_setting(key: str, body: SettingUpdateRequest, admin: dict = Depends(require_admin)):
    """시스템 설정 변경."""
    allowed_keys = {
        "default_session_hours", "max_session_hours",
        "team_slack_webhook", "unclaimed_alert_minutes",
    }
    if key not in allowed_keys:
        raise HTTPException(status_code=400, detail=f"허용되지 않은 설정 키: {key}")

    async with get_session() as session:
        await session.execute(
            text("INSERT INTO app_settings (key, value) VALUES (:k, :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
            {"k": key, "v": body.value},
        )
        await session.commit()

    logger.info("Admin %s updated setting %s=%s", admin["email"], key, body.value)
    return {"key": key, "value": body.value}


# ── Graph API 권한(scope) ──────────────────────────────────────────────────────

@router.get("/scopes")
async def get_scopes(user: dict = Depends(require_login)):
    """현재 활성 scope 목록과 전체 권한 카탈로그 반환. 모든 로그인 사용자 조회 가능."""
    active_scope_str = await get_active_scope()
    active_set = set(active_scope_str.split())

    catalog = [
        {
            **item,
            "enabled": item["scope"] in active_set,
        }
        for item in SCOPE_CATALOG
    ]
    return {
        "active_scope": active_scope_str,
        "catalog": catalog,
        "is_default": active_scope_str == settings.scope,
    }


@router.patch("/scopes")
async def update_scopes(body: ScopeUpdateRequest, admin: dict = Depends(require_admin)):
    """scope 변경 (admin 전용). 변경 후 Outlook 재연동 시 반영."""
    requested = set(body.scopes)

    # 유효하지 않은 scope 거부
    invalid = requested - VALID_SCOPES
    if invalid:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 권한: {', '.join(invalid)}")

    # 필수 scope 강제 포함
    final = requested | REQUIRED_SCOPES
    scope_str = " ".join(sorted(final, key=lambda s: list(VALID_SCOPES).index(s) if s in VALID_SCOPES else 99))

    async with get_session() as session:
        await session.execute(
            text("INSERT INTO app_settings (key, value) VALUES ('graph_scopes', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
            {"v": scope_str},
        )
        await session.commit()

    logger.info("Admin %s updated graph_scopes: %s", admin["email"], scope_str)
    return {
        "active_scope": scope_str,
        "message": "권한이 저장되었습니다. Outlook을 재연동해야 새 권한이 적용됩니다.",
        "requires_reconnect": True,
    }
