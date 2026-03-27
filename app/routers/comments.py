"""
팀 댓글 + @멘션 API
- GET  /api/threads/{cid}/comments        : 댓글 목록 (페이지네이션)
- POST /api/threads/{cid}/comments        : 댓글 작성 (@멘션 자동 파싱)
- DELETE /api/threads/{cid}/comments/{id} : 댓글 삭제 (작성자/admin)
- GET  /api/mentions/unread               : 읽지 않은 @멘션 목록
- PATCH /api/mentions/{id}/read           : 멘션 읽음 처리
"""
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.database import get_session
from app.middleware import require_login
from app.services.websocket_manager import ws_manager

logger = logging.getLogger("autoreply.comments")

router = APIRouter(tags=["comments"])


class CommentCreate(BaseModel):
    content: str
    project_id: Optional[str] = None


def _extract_mentions(content: str) -> list[str]:
    """
    @[표시이름](user_id) 형태 또는 @user_id 형태에서 user_id 추출.
    예: "@[dominic](abc-123)" → ["abc-123"]
         "@김철수" → 이름으로 처리 (별도 조회 필요)
    단순하게 @(...) 패턴의 괄호 안 UUID 형태를 추출.
    """
    pattern = r"@\[.*?\]\(([^)]+)\)"
    return re.findall(pattern, content)


@router.get("/api/threads/{conversation_id}/comments")
async def list_comments(
    conversation_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_login),
):
    offset = (page - 1) * per_page

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT c.id, c.conversation_id, c.project_id, c.user_id,
                   c.content, c.created_at,
                   u.display_name, u.email
            FROM comments c
            JOIN users u ON u.user_id = c.user_id
            WHERE c.conversation_id = :cid
            ORDER BY c.created_at ASC
            LIMIT :limit OFFSET :offset
        """), {"cid": conversation_id, "limit": per_page, "offset": offset})
        rows = result.mappings().all()

        count_result = await session.execute(
            text("SELECT COUNT(*) as cnt FROM comments WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        total = count_result.mappings().first()["cnt"]

        # 각 댓글의 멘션 목록
        comments = []
        for row in rows:
            rd = dict(row)
            mention_result = await session.execute(text("""
                SELECT mn.id, mn.mentioned_user_id, mn.is_read,
                       u2.display_name as mentioned_name
                FROM mentions mn
                JOIN users u2 ON u2.user_id = mn.mentioned_user_id
                WHERE mn.comment_id = :cid
            """), {"cid": rd["id"]})
            rd["mentions"] = [dict(m) for m in mention_result.mappings().all()]
            comments.append(rd)

    return {"comments": comments, "total": total, "page": page, "per_page": per_page}


@router.post("/api/threads/{conversation_id}/comments", status_code=201)
async def create_comment(
    conversation_id: str,
    body: CommentCreate,
    user: dict = Depends(require_login),
):
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="댓글 내용을 입력해주세요.")

    comment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # @멘션 파싱
    mentioned_user_ids = _extract_mentions(body.content)

    async with get_session() as session:
        # 쓰레드 존재 확인
        t_result = await session.execute(
            text("SELECT conversation_id FROM threads WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        if not t_result.mappings().first():
            raise HTTPException(status_code=404, detail="Thread not found")

        # 댓글 저장
        await session.execute(text("""
            INSERT INTO comments (id, conversation_id, project_id, user_id, content, created_at)
            VALUES (:id, :cid, :pid, :uid, :content, :ca)
        """), {
            "id": comment_id, "cid": conversation_id, "pid": body.project_id,
            "uid": user["user_id"], "content": body.content, "ca": now,
        })

        # 멘션 저장 + 알림
        valid_mentions = []
        for mentioned_uid in set(mentioned_user_ids):
            if mentioned_uid == user["user_id"]:
                continue  # 자기 자신 멘션 제외

            # 사용자 존재 확인
            u_result = await session.execute(
                text("SELECT user_id, display_name FROM users WHERE user_id=:uid AND is_active=1"),
                {"uid": mentioned_uid},
            )
            u_row = u_result.mappings().first()
            if not u_row:
                continue

            mention_id = str(uuid.uuid4())
            await session.execute(text("""
                INSERT INTO mentions (id, comment_id, mentioned_user_id, is_read, notified_slack, created_at)
                VALUES (:id, :cid, :uid, 0, 0, :ca)
            """), {"id": mention_id, "cid": comment_id, "uid": mentioned_uid, "ca": now})
            valid_mentions.append({"user_id": mentioned_uid, "display_name": u_row["display_name"]})

        await session.commit()

    # WebSocket으로 멘션된 사용자에게 알림
    for m in valid_mentions:
        await ws_manager.broadcast_to_user(m["user_id"], {
            "type": "mention",
            "data": {
                "comment_id": comment_id,
                "conversation_id": conversation_id,
                "from_name": user.get("display_name", ""),
                "content_preview": body.content[:80],
            },
        })

    # 프로젝트 구성원 전체에게 새 댓글 알림
    if body.project_id:
        await ws_manager.broadcast_to_project(body.project_id, {
            "type": "new_comment",
            "data": {
                "comment_id": comment_id,
                "conversation_id": conversation_id,
                "user_name": user.get("display_name", ""),
            },
        })

    logger.info("Comment created: %s on thread %s by %s", comment_id, conversation_id, user["user_id"])
    return {
        "id": comment_id,
        "conversation_id": conversation_id,
        "user_id": user["user_id"],
        "display_name": user.get("display_name", ""),
        "content": body.content,
        "created_at": now,
        "mentions": valid_mentions,
    }


@router.delete("/api/threads/{conversation_id}/comments/{comment_id}")
async def delete_comment(
    conversation_id: str,
    comment_id: str,
    user: dict = Depends(require_login),
):
    async with get_session() as session:
        result = await session.execute(
            text("SELECT user_id FROM comments WHERE id=:cid AND conversation_id=:tid"),
            {"cid": comment_id, "tid": conversation_id},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다.")

        if row["user_id"] != user["user_id"] and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="자신이 작성한 댓글만 삭제할 수 있습니다.")

        # mentions ON DELETE CASCADE → 자동 삭제
        await session.execute(
            text("DELETE FROM mentions WHERE comment_id=:cid"),
            {"cid": comment_id},
        )
        await session.execute(
            text("DELETE FROM comments WHERE id=:cid"),
            {"cid": comment_id},
        )
        await session.commit()

    return {"success": True}


# ── 멘션 읽음 처리 ─────────────────────────────────────────────────────────────

@router.get("/api/mentions/unread")
async def list_unread_mentions(user: dict = Depends(require_login)):
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT mn.id, mn.comment_id, mn.is_read, mn.created_at,
                   c.conversation_id, c.content, c.project_id,
                   u.display_name as from_name
            FROM mentions mn
            JOIN comments c ON c.id = mn.comment_id
            JOIN users u ON u.user_id = c.user_id
            WHERE mn.mentioned_user_id = :uid AND mn.is_read = 0
            ORDER BY mn.created_at DESC
            LIMIT 50
        """), {"uid": user["user_id"]})
        rows = result.mappings().all()

    return {"mentions": [dict(r) for r in rows]}


@router.patch("/api/mentions/{mention_id}/read")
async def mark_mention_read(mention_id: str, user: dict = Depends(require_login)):
    async with get_session() as session:
        result = await session.execute(
            text("SELECT mentioned_user_id FROM mentions WHERE id=:mid"),
            {"mid": mention_id},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="멘션을 찾을 수 없습니다.")
        if row["mentioned_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

        await session.execute(
            text("UPDATE mentions SET is_read=1 WHERE id=:mid"),
            {"mid": mention_id},
        )
        await session.commit()

    return {"success": True}
