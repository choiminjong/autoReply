import json
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional

from app.database import get_session
from app.middleware import require_login
from app.models.schemas import (
    AttachmentSchema, FolderMoveRequest, MessageSchema, ReplyRequest,
    StatusUpdateRequest, ThreadListItem, ThreadSchema,
)
from app.services.auth_service import get_valid_access_token
from app.services.outlook import (
    _parse_message, download_attachment_bytes, fetch_conversation_messages,
    fetch_message_attachments, move_conversation, send_reply,
    upsert_message, upsert_thread,
)
from app.services.websocket_manager import ws_manager

logger = logging.getLogger("autoreply.threads")

router = APIRouter(prefix="/api/threads", tags=["threads"])
attachments_router = APIRouter(prefix="/api/messages", tags=["attachments"])

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def _resolve_attachments(message_id: str, body: str) -> tuple[str, list[AttachmentSchema]]:
    """
    has_attachments=True인 메시지에 대해 Graph API에서 첨부파일을 가져온다.
    - 인라인 이미지(isInline=True): body HTML의 cid: 참조를 data URI로 치환
    - 일반 파일(isInline=False): AttachmentSchema 목록으로 반환
    """
    try:
        att_data = await fetch_message_attachments(message_id)
    except Exception as e:
        logger.warning("fetch_message_attachments error for %s: %s", message_id[:20], e)
        return body, []

    import re
    for inline in att_data.get("inline", []):
        cid = inline["content_id"]
        att_id = inline["att_id"]
        # 브라우저가 세션 쿠키로 직접 로드할 수 있는 프록시 URL로 치환.
        # contentBytes 없이도 크기 무관하게 동작.
        proxy_url = f"/api/messages/{message_id}/attachments/{att_id}"
        body = re.sub(re.escape(f"cid:{cid}"), proxy_url, body, flags=re.IGNORECASE)

    logger.info(
        "_resolve_attachments msg=%s inline=%d files=%d",
        message_id[:30], len(att_data.get("inline", [])), len(att_data.get("files", [])),
    )

    files = [
        AttachmentSchema(
            id=f["id"],
            name=f["name"],
            size=f["size"],
            content_type=f["content_type"],
            is_inline=False,
        )
        for f in att_data.get("files", [])
    ]

    return body, files


async def _auto_map_project(session, conversation_id: str, recipients_json: str):
    """
    메일링리스트 기반 프로젝트 자동 매핑.
    수신자 목록에 프로젝트의 mailing_list가 포함되면 해당 project_id로 설정.
    """
    try:
        recipients = json.loads(recipients_json or "[]")
        all_emails = [r.get("email", "").lower() for r in recipients]
    except Exception:
        return

    result = await session.execute(text("SELECT project_id, mailing_list FROM projects WHERE mailing_list != ''"))
    for row in result.mappings().all():
        ml = (row["mailing_list"] or "").lower()
        if ml and ml in all_emails:
            await session.execute(
                text("UPDATE threads SET project_id=:pid WHERE conversation_id=:cid AND project_id IS NULL"),
                {"pid": row["project_id"], "cid": conversation_id},
            )
            return


@router.get("")
async def list_threads(
    status: str = Query(None),
    folder_id: str = Query(None),
    project_id: str = Query(None),
    view: str = Query("all"),   # "all" | "unclaimed" | "mine"
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_login),
):

    offset = (page - 1) * per_page

    async with get_session() as session:
        conditions = []
        params: dict = {"limit": per_page, "offset": offset}

        if status:
            conditions.append("t.status = :status")
            params["status"] = status

        if project_id:
            conditions.append("t.project_id = :project_id")
            params["project_id"] = project_id

        if view == "unclaimed":
            conditions.append("t.claimed_by IS NULL")
        elif view == "mine":
            conditions.append("t.claimed_by = :view_uid")
            params["view_uid"] = user["user_id"]

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        result = await session.execute(
            text(f"""
                SELECT
                    t.conversation_id,
                    t.status,
                    t.latest_at,
                    t.has_new_reply,
                    t.project_id,
                    t.claimed_by,
                    t.user_id as owner_user_id,
                    COALESCE(t.subject, '') as subject,
                    COUNT(m.id) as message_count,
                    (SELECT m2.sender FROM messages m2
                     WHERE m2.conversation_id = t.conversation_id
                     ORDER BY m2.received_at DESC LIMIT 1) as latest_sender,
                    (SELECT m2.sender_email FROM messages m2
                     WHERE m2.conversation_id = t.conversation_id
                     ORDER BY m2.received_at DESC LIMIT 1) as latest_sender_email,
                    (SELECT m2.body_preview FROM messages m2
                     WHERE m2.conversation_id = t.conversation_id
                     ORDER BY m2.received_at DESC LIMIT 1) as body_preview,
                    (SELECT m2.has_attachments FROM messages m2
                     WHERE m2.conversation_id = t.conversation_id
                     ORDER BY m2.received_at DESC LIMIT 1) as has_attachments,
                    (SELECT m2.folder_name FROM messages m2
                     WHERE m2.conversation_id = t.conversation_id
                     ORDER BY m2.received_at ASC LIMIT 1) as primary_folder,
                    u.display_name as claimed_by_name
                FROM threads t
                LEFT JOIN messages m ON m.conversation_id = t.conversation_id
                LEFT JOIN users u ON u.user_id = t.claimed_by
                {where}
                GROUP BY t.conversation_id
                ORDER BY t.latest_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.mappings().all()

        # 총 개수
        count_result = await session.execute(
            text(f"SELECT COUNT(DISTINCT t.conversation_id) as cnt FROM threads t {where}"),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
        total = count_result.mappings().first()["cnt"]

    result_list = []
    for rd in rows:
        rd = dict(rd)

        # 폴더 불일치 감지
        async with get_session() as session:
            fld_result = await session.execute(
                text("SELECT DISTINCT folder_name FROM messages WHERE conversation_id=:cid AND folder_name != ''"),
                {"cid": rd["conversation_id"]},
            )
            folder_rows = fld_result.mappings().all()
        has_mismatch = len(folder_rows) > 1

        # 폴더 필터
        if folder_id:
            async with get_session() as session:
                flt_result = await session.execute(
                    text("SELECT id FROM messages WHERE conversation_id=:cid AND folder_id=:fid LIMIT 1"),
                    {"cid": rd["conversation_id"], "fid": folder_id},
                )
                if not flt_result.mappings().first():
                    continue

        item = ThreadListItem(
            conversation_id=rd["conversation_id"],
            subject=rd.get("subject") or rd.get("body_preview", "")[:60],
            status=rd["status"],
            primary_folder=rd.get("primary_folder") or "",
            has_folder_mismatch=has_mismatch,
            latest_at=rd.get("latest_at") or "",
            has_new_reply=bool(rd.get("has_new_reply")),
            message_count=rd.get("message_count", 0),
            latest_sender=rd.get("latest_sender") or "",
            latest_sender_email=rd.get("latest_sender_email") or "",
            body_preview=rd.get("body_preview") or "",
            has_attachments=bool(rd.get("has_attachments")),
        )
        d = item.model_dump()
        d["project_id"] = rd.get("project_id")
        d["claimed_by"] = rd.get("claimed_by")
        d["claimed_by_name"] = rd.get("claimed_by_name")
        result_list.append(d)

    return {
        "threads": result_list,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{conversation_id}")
async def get_thread(conversation_id: str, user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    logger.info("get_thread: conv=%s token_available=%s", conversation_id[:30], bool(token))
    if not token:
        logger.warning("get_thread: Outlook 토큰 없음 - DB fallback 사용")

    async with get_session() as session:
        result = await session.execute(
            text("SELECT * FROM threads WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        thread_row = result.mappings().first()

    if not thread_row:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread = dict(thread_row)

    # Graph API에서 전체 쓰레드 메일 온디맨드 조회 (토큰 있을 때만)
    raw_messages = []
    my_email = ""
    if token:
        raw_messages = await fetch_conversation_messages(conversation_id)
        async with httpx.AsyncClient(timeout=10) as client:
            mr = await client.get(
                f"{GRAPH_BASE}/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if mr.status_code == 200:
                my_email = mr.json().get("mail") or mr.json().get("userPrincipalName", "")

    # DB에서 folder_id/folder_name 보완용 맵
    async with get_session() as session:
        fm_result = await session.execute(
            text("SELECT id, folder_id, folder_name FROM messages WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        db_rows = fm_result.mappings().all()
        folder_map = {r["id"]: (r["folder_id"], r["folder_name"]) for r in db_rows}

    messages = []

    if raw_messages:
        # Graph 결과가 있으면 Graph 데이터를 우선 사용
        for msg in raw_messages:
            fid, fname = folder_map.get(msg["id"], (msg.get("parentFolderId", ""), ""))
            if not fname:
                async with get_session() as session:
                    fn_result = await session.execute(
                        text("SELECT folder_name FROM sync_folders WHERE folder_id=:fid"),
                        {"fid": fid},
                    )
                    fn_row = fn_result.mappings().first()
                    fname = fn_row["folder_name"] if fn_row else ""

            parsed = _parse_message(msg, fid, fname, my_email)

            try:
                to_r = json.loads(parsed["to_recipients"])
            except Exception:
                to_r = []
            try:
                cc_r = json.loads(parsed["cc_recipients"])
            except Exception:
                cc_r = []

            body = parsed["body"]
            att_list: list[AttachmentSchema] = []
            _has_att = parsed["has_attachments"]
            _has_cid = "cid:" in body.lower()
            # has_attachments 플래그 또는 body에 cid: 참조가 있으면 attachment 조회
            if token and (_has_att or _has_cid):
                body, att_list = await _resolve_attachments(parsed["id"], body)

            messages.append(
                MessageSchema(
                    id=parsed["id"],
                    conversation_id=parsed["conversation_id"],
                    folder_id=parsed["folder_id"],
                    folder_name=parsed["folder_name"],
                    sender=parsed["sender"],
                    sender_email=parsed["sender_email"],
                    to_recipients=to_r,
                    cc_recipients=cc_r,
                    received_at=parsed["received_at"],
                    body_preview=parsed["body_preview"],
                    body=body,
                    is_read=bool(parsed["is_read"]),
                    has_attachments=bool(parsed["has_attachments"]),
                    is_from_me=bool(parsed["is_from_me"]),
                    attachments=att_list,
                )
            )
    else:
        # Graph 결과가 없으면 DB messages 테이블에서 폴백 조회
        logger.warning("Graph returned no messages for %s — falling back to DB", conversation_id)
        async with get_session() as session:
            db_result = await session.execute(
                text("""
                    SELECT m.*, sf.folder_name AS sf_folder_name
                    FROM messages m
                    LEFT JOIN sync_folders sf ON sf.folder_id = m.folder_id
                    WHERE m.conversation_id = :cid
                    ORDER BY m.received_at ASC
                """),
                {"cid": conversation_id},
            )
            db_messages = db_result.mappings().all()

        for row in db_messages:
            row = dict(row)
            try:
                to_r = json.loads(row.get("to_recipients") or "[]")
            except Exception:
                to_r = []
            try:
                cc_r = json.loads(row.get("cc_recipients") or "[]")
            except Exception:
                cc_r = []

            fname = row.get("folder_name") or row.get("sf_folder_name") or ""
            body = row.get("body") or row.get("body_preview") or ""
            att_list = []
            if token and (row.get("has_attachments") or "cid:" in body.lower()):
                body, att_list = await _resolve_attachments(row["id"], body)

            messages.append(
                MessageSchema(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    folder_id=row.get("folder_id") or "",
                    folder_name=fname,
                    sender=row.get("sender") or "",
                    sender_email=row.get("sender_email") or "",
                    to_recipients=to_r,
                    cc_recipients=cc_r,
                    received_at=row.get("received_at") or "",
                    body_preview=row.get("body_preview") or "",
                    body=body,
                    is_read=bool(row.get("is_read", 0)),
                    has_attachments=bool(row.get("has_attachments", 0)),
                    is_from_me=bool(row.get("is_from_me", 0)),
                    attachments=att_list,
                )
            )

    primary_folder = messages[0].folder_name if messages else ""
    has_mismatch = len({m.folder_name for m in messages if m.folder_name}) > 1

    return ThreadSchema(
        conversation_id=conversation_id,
        subject=thread.get("subject") or (messages[0].body_preview[:60] if messages else ""),
        status=thread["status"],
        primary_folder=primary_folder,
        has_folder_mismatch=has_mismatch,
        latest_at=thread.get("latest_at") or "",
        has_new_reply=bool(thread.get("has_new_reply")),
        message_count=len(messages),
        messages=messages,
    )


@router.patch("/{conversation_id}/status")
async def update_status(
    conversation_id: str,
    body: StatusUpdateRequest,
    user: dict = Depends(require_login),
):
    async with get_session() as session:
        await session.execute(
            text("UPDATE threads SET status=:status, has_new_reply=0 WHERE conversation_id=:cid"),
            {"status": body.status.value, "cid": conversation_id},
        )
        await session.commit()

    await ws_manager.broadcast({
        "type": "status_change",
        "data": {"conversation_id": conversation_id, "status": body.status.value},
    })
    return {"conversation_id": conversation_id, "status": body.status.value}


@router.post("/{conversation_id}/reply")
async def reply_to_thread(
    conversation_id: str,
    body: ReplyRequest,
    user: dict = Depends(require_login),
):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    async with get_session() as session:
        result = await session.execute(
            text("SELECT id FROM messages WHERE conversation_id=:cid ORDER BY received_at DESC LIMIT 1"),
            {"cid": conversation_id},
        )
        row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="No messages in thread")

    latest_message_id = row["id"]

    try:
        sent = await send_reply(
            message_id=latest_message_id,
            body=body.body,
            reply_type=body.reply_type,
            to_recipients=[r.model_dump() for r in body.to_recipients],
            cc_recipients=[r.model_dump() for r in body.cc_recipients],
            bcc_recipients=[r.model_dump() for r in body.bcc_recipients],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if sent.get("id"):
        my_email = ""
        async with httpx.AsyncClient(timeout=10) as client:
            mr = await client.get(
                f"{GRAPH_BASE}/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if mr.status_code == 200:
                my_email = mr.json().get("mail") or mr.json().get("userPrincipalName", "")

        async with get_session() as session:
            msg_data = _parse_message(sent, "", "", my_email)
            msg_data["conversation_id"] = conversation_id
            await upsert_thread(session, msg_data)
            await upsert_message(session, msg_data)
            # 회신 시 자동 클레임
            await _auto_claim_on_reply(session, conversation_id, user["user_id"])
            await session.commit()

    await ws_manager.broadcast({
        "type": "new_reply_sent",
        "data": {"conversation_id": conversation_id},
    })
    return {"success": True}


@router.post("/{conversation_id}/move")
async def move_thread(
    conversation_id: str,
    body: FolderMoveRequest,
    user: dict = Depends(require_login),
):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    moved = await move_conversation(conversation_id, body.destination_folder_id)

    await ws_manager.broadcast({
        "type": "folder_moved",
        "data": {"conversation_id": conversation_id, "folder_id": body.destination_folder_id},
    })
    return {"moved": moved}


# ── 쓰레드-프로젝트 수동 매핑 ────────────────────────────────────────────────

class ProjectAssignRequest(BaseModel):
    project_id: str | None = None


@router.patch("/{conversation_id}/project")
async def assign_project(
    conversation_id: str,
    body: ProjectAssignRequest,
    user: dict = Depends(require_login),
):
    """수동으로 쓰레드에 프로젝트 지정 (또는 해제)."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT conversation_id FROM threads WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        if not result.mappings().first():
            raise HTTPException(status_code=404, detail="Thread not found")

        await session.execute(
            text("UPDATE threads SET project_id=:pid WHERE conversation_id=:cid"),
            {"pid": body.project_id, "cid": conversation_id},
        )
        await session.commit()

    return {"conversation_id": conversation_id, "project_id": body.project_id}


# ── 클레임 시스템 ─────────────────────────────────────────────────────────────

@router.post("/{conversation_id}/claim")
async def claim_thread(conversation_id: str, user: dict = Depends(require_login)):
    """
    수동 클레임. claimed_by가 NULL일 때만 성공 (낙관적 잠금).
    이미 클레임된 경우 409 반환.
    """
    async with get_session() as session:
        result = await session.execute(
            text("SELECT claimed_by FROM threads WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")

        if row["claimed_by"] is not None:
            # 이미 클레임된 경우 담당자 이름 조회
            u_result = await session.execute(
                text("SELECT display_name FROM users WHERE user_id=:uid"),
                {"uid": row["claimed_by"]},
            )
            u_row = u_result.mappings().first()
            claimant = u_row["display_name"] if u_row else row["claimed_by"]
            raise HTTPException(
                status_code=409,
                detail=f"이미 {claimant}님이 처리 중입니다.",
            )

        # 낙관적 잠금: WHERE claimed_by IS NULL 조건으로 UPDATE
        update_result = await session.execute(
            text("""
                UPDATE threads SET claimed_by=:uid
                WHERE conversation_id=:cid AND claimed_by IS NULL
            """),
            {"uid": user["user_id"], "cid": conversation_id},
        )
        if update_result.rowcount == 0:
            raise HTTPException(status_code=409, detail="동시에 다른 팀원이 클레임했습니다.")
        await session.commit()

    await ws_manager.broadcast({
        "type": "claim_changed",
        "data": {
            "conversation_id": conversation_id,
            "claimed_by": user["user_id"],
            "claimed_by_name": user.get("display_name", ""),
        },
    })
    return {"conversation_id": conversation_id, "claimed_by": user["user_id"]}


@router.delete("/{conversation_id}/claim")
async def unclaim_thread(conversation_id: str, user: dict = Depends(require_login)):
    """클레임 해제. 자신이 클레임한 건이거나 admin만 가능."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT claimed_by FROM threads WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")

        if row["claimed_by"] != user["user_id"] and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="자신이 클레임한 건만 해제할 수 있습니다.")

        await session.execute(
            text("UPDATE threads SET claimed_by=NULL WHERE conversation_id=:cid"),
            {"cid": conversation_id},
        )
        await session.commit()

    await ws_manager.broadcast({
        "type": "claim_changed",
        "data": {"conversation_id": conversation_id, "claimed_by": None},
    })
    return {"conversation_id": conversation_id, "claimed_by": None}


# ── 회신 시 자동 클레임 ────────────────────────────────────────────────────────

async def _auto_claim_on_reply(session, conversation_id: str, user_id: str):
    """회신 발송 시 자동 클레임 (claimed_by가 NULL인 경우만)."""
    await session.execute(
        text("""
            UPDATE threads SET claimed_by=:uid
            WHERE conversation_id=:cid AND claimed_by IS NULL
        """),
        {"uid": user_id, "cid": conversation_id},
    )


# ── 첨부파일 다운로드 프록시 ───────────────────────────────────────────────────

@attachments_router.get("/{message_id}/attachments/{attachment_id}")
async def download_attachment(
    message_id: str,
    attachment_id: str,
    user: dict = Depends(require_login),
):
    """Graph API에서 첨부파일을 가져와 클라이언트에 스트리밍."""
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    try:
        content_bytes, content_type, filename = await download_attachment_bytes(message_id, attachment_id)
    except Exception as e:
        logger.error("download_attachment error: %s", e)
        raise HTTPException(status_code=500, detail="첨부파일을 가져올 수 없습니다.")

    from urllib.parse import quote
    encoded_name = quote(filename, safe="")
    # 이미지는 브라우저가 <img> 태그로 직접 렌더링할 수 있게 inline, 나머지는 다운로드
    if content_type.startswith("image/"):
        disposition = "inline"
    else:
        disposition = f"attachment; filename*=UTF-8''{encoded_name}"
    return Response(
        content=content_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(content_bytes)),
        },
    )
