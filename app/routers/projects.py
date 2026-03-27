"""
프로젝트 CRUD + 멤버 관리 API
- GET  /api/projects          : 내 프로젝트 목록
- POST /api/projects          : 프로젝트 생성 (생성자 = owner 자동)
- GET  /api/projects/{id}     : 프로젝트 상세
- PATCH /api/projects/{id}    : 프로젝트 수정 (owner/admin)
- DELETE /api/projects/{id}   : 프로젝트 삭제 (owner/admin)
- GET  /api/projects/{id}/members       : 멤버 목록
- POST /api/projects/{id}/members       : 멤버 추가 (owner/admin)
- DELETE /api/projects/{id}/members/{uid}: 멤버 제거 (owner/admin)
- PATCH /api/projects/{id}/members/{uid}: 역할 변경 (owner/admin)
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional

from app.database import get_session
from app.middleware import require_login

logger = logging.getLogger("autoreply.projects")

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── 요청 스키마 ───────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    mailing_list: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    mailing_list: Optional[str] = None


class MemberAdd(BaseModel):
    user_id: str
    role: str = "member"  # "owner" | "member"


class MemberRoleUpdate(BaseModel):
    role: str


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

async def _get_project_or_404(session, project_id: str) -> dict:
    result = await session.execute(
        text("SELECT * FROM projects WHERE project_id = :pid"),
        {"pid": project_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    return dict(row)


async def _require_project_role(session, project_id: str, user: dict, roles: list[str]):
    """project 내 역할 체크. admin은 항상 통과."""
    if user.get("role") == "admin":
        return
    result = await session.execute(
        text("SELECT role FROM project_members WHERE project_id=:pid AND user_id=:uid"),
        {"pid": project_id, "uid": user["user_id"]},
    )
    row = result.mappings().first()
    if not row or row["role"] not in roles:
        raise HTTPException(status_code=403, detail="이 작업을 수행할 권한이 없습니다.")


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("")
async def list_projects(user: dict = Depends(require_login)):
    """내 프로젝트 목록 (admin은 전체)."""
    async with get_session() as session:
        if user.get("role") == "admin":
            result = await session.execute(text("""
                SELECT p.*,
                    (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id = p.project_id) as member_count,
                    (SELECT COUNT(*) FROM threads t WHERE t.project_id = p.project_id AND t.claimed_by IS NULL) as unclaimed_count,
                    (SELECT pm2.role FROM project_members pm2 WHERE pm2.project_id = p.project_id AND pm2.user_id = :uid) as my_role
                FROM projects p
                ORDER BY p.created_at DESC
            """), {"uid": user["user_id"]})
        else:
            result = await session.execute(text("""
                SELECT p.*,
                    (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id = p.project_id) as member_count,
                    (SELECT COUNT(*) FROM threads t WHERE t.project_id = p.project_id AND t.claimed_by IS NULL) as unclaimed_count,
                    pm2.role as my_role
                FROM projects p
                JOIN project_members pm2 ON pm2.project_id = p.project_id AND pm2.user_id = :uid
                ORDER BY p.created_at DESC
            """), {"uid": user["user_id"]})
        rows = result.mappings().all()

    return {"projects": [dict(r) for r in rows]}


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, user: dict = Depends(require_login)):
    project_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    async with get_session() as session:
        await session.execute(text("""
            INSERT INTO projects (project_id, name, description, mailing_list, created_by, created_at)
            VALUES (:pid, :name, :desc, :ml, :cb, :ca)
        """), {
            "pid": project_id, "name": body.name, "desc": body.description,
            "ml": body.mailing_list, "cb": user["user_id"], "ca": now,
        })
        # 생성자는 자동으로 owner
        await session.execute(text("""
            INSERT INTO project_members (project_id, user_id, role, joined_at)
            VALUES (:pid, :uid, 'owner', :ja)
        """), {"pid": project_id, "uid": user["user_id"], "ja": now})
        await session.commit()

    logger.info("Project created: %s by %s", project_id, user["user_id"])
    return {"project_id": project_id, "name": body.name}


@router.get("/{project_id}")
async def get_project(project_id: str, user: dict = Depends(require_login)):
    async with get_session() as session:
        project = await _get_project_or_404(session, project_id)

        # 접근 권한: admin이거나 멤버여야 함
        if user.get("role") != "admin":
            mem_result = await session.execute(
                text("SELECT role FROM project_members WHERE project_id=:pid AND user_id=:uid"),
                {"pid": project_id, "uid": user["user_id"]},
            )
            if not mem_result.mappings().first():
                raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

    return project


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, user: dict = Depends(require_login)):
    async with get_session() as session:
        await _get_project_or_404(session, project_id)
        await _require_project_role(session, project_id, user, ["owner"])

        updates = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.description is not None:
            updates["description"] = body.description
        if body.mailing_list is not None:
            updates["mailing_list"] = body.mailing_list

        if updates:
            set_clause = ", ".join(f"{k}=:{k}" for k in updates)
            updates["pid"] = project_id
            await session.execute(
                text(f"UPDATE projects SET {set_clause} WHERE project_id=:pid"),
                updates,
            )
            await session.commit()

    return {"success": True}


@router.delete("/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(require_login)):
    async with get_session() as session:
        await _get_project_or_404(session, project_id)
        await _require_project_role(session, project_id, user, ["owner"])

        # threads의 project_id를 NULL로
        await session.execute(
            text("UPDATE threads SET project_id=NULL WHERE project_id=:pid"),
            {"pid": project_id},
        )
        # 댓글은 project_id만 NULL로 (내용 보존)
        await session.execute(
            text("UPDATE comments SET project_id=NULL WHERE project_id=:pid"),
            {"pid": project_id},
        )
        # project_members, 프로젝트 자체 삭제 (ON DELETE CASCADE)
        await session.execute(
            text("DELETE FROM project_members WHERE project_id=:pid"),
            {"pid": project_id},
        )
        await session.execute(
            text("DELETE FROM projects WHERE project_id=:pid"),
            {"pid": project_id},
        )
        await session.commit()

    logger.info("Project deleted: %s by %s", project_id, user["user_id"])
    return {"success": True}


# ── 멤버 관리 ─────────────────────────────────────────────────────────────────

@router.get("/{project_id}/members")
async def list_members(project_id: str, user: dict = Depends(require_login)):
    async with get_session() as session:
        await _get_project_or_404(session, project_id)

        result = await session.execute(text("""
            SELECT pm.user_id, pm.role, pm.joined_at,
                   u.display_name, u.email, u.is_active,
                   (SELECT COUNT(*) > 0 FROM outlook_tokens ot WHERE ot.user_id = pm.user_id) as has_outlook
            FROM project_members pm
            JOIN users u ON u.user_id = pm.user_id
            WHERE pm.project_id = :pid
            ORDER BY pm.role DESC, pm.joined_at ASC
        """), {"pid": project_id})
        rows = result.mappings().all()

    return {"members": [dict(r) for r in rows]}


@router.post("/{project_id}/members", status_code=201)
async def add_member(project_id: str, body: MemberAdd, user: dict = Depends(require_login)):
    async with get_session() as session:
        await _get_project_or_404(session, project_id)
        await _require_project_role(session, project_id, user, ["owner"])

        # 사용자 존재 확인
        u_result = await session.execute(
            text("SELECT user_id FROM users WHERE user_id=:uid AND is_active=1"),
            {"uid": body.user_id},
        )
        if not u_result.mappings().first():
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

        # 중복 체크 후 upsert
        await session.execute(text("""
            INSERT OR REPLACE INTO project_members (project_id, user_id, role, joined_at)
            VALUES (:pid, :uid, :role, :ja)
        """), {
            "pid": project_id, "uid": body.user_id,
            "role": body.role, "ja": datetime.utcnow().isoformat(),
        })
        await session.commit()

    return {"success": True}


@router.delete("/{project_id}/members/{target_user_id}")
async def remove_member(project_id: str, target_user_id: str, user: dict = Depends(require_login)):
    async with get_session() as session:
        await _get_project_or_404(session, project_id)
        await _require_project_role(session, project_id, user, ["owner"])

        # owner 최소 1명 유지
        owner_result = await session.execute(
            text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id=:pid AND role='owner'"),
            {"pid": project_id},
        )
        owner_count = owner_result.mappings().first()["cnt"]

        # 제거 대상이 owner이고 남은 owner가 1명이면 거부
        role_result = await session.execute(
            text("SELECT role FROM project_members WHERE project_id=:pid AND user_id=:uid"),
            {"pid": project_id, "uid": target_user_id},
        )
        target_role_row = role_result.mappings().first()
        if target_role_row and target_role_row["role"] == "owner" and owner_count <= 1:
            raise HTTPException(status_code=400, detail="프로젝트에 최소 1명의 owner가 있어야 합니다.")

        # 해당 사용자의 클레임 해제
        await session.execute(
            text("UPDATE threads SET claimed_by=NULL WHERE project_id=:pid AND claimed_by=:uid"),
            {"pid": project_id, "uid": target_user_id},
        )

        await session.execute(
            text("DELETE FROM project_members WHERE project_id=:pid AND user_id=:uid"),
            {"pid": project_id, "uid": target_user_id},
        )
        await session.commit()

    return {"success": True}


@router.patch("/{project_id}/members/{target_user_id}")
async def update_member_role(
    project_id: str,
    target_user_id: str,
    body: MemberRoleUpdate,
    user: dict = Depends(require_login),
):
    if body.role not in ("owner", "member"):
        raise HTTPException(status_code=400, detail="role은 'owner' 또는 'member'여야 합니다.")

    async with get_session() as session:
        await _get_project_or_404(session, project_id)
        await _require_project_role(session, project_id, user, ["owner"])

        await session.execute(
            text("UPDATE project_members SET role=:role WHERE project_id=:pid AND user_id=:uid"),
            {"role": body.role, "pid": project_id, "uid": target_user_id},
        )
        await session.commit()

    return {"success": True}
