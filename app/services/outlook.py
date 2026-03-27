"""
Microsoft Graph API 연동 서비스.
- 폴더 목록 조회
- 메일 Full Sync / Delta Sync
- 메일 회신 / 전달
- 폴더 이동 (양방향 싱크)
- 수신자 자동완성
"""
import json
import logging
import httpx
from datetime import datetime, timezone
from sqlalchemy import text

from app.services.auth_service import get_valid_access_token
from app.database import get_session

logger = logging.getLogger("autoreply.outlook")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

MESSAGE_SELECT = (
    "id,conversationId,subject,sender,toRecipients,ccRecipients,"
    "receivedDateTime,bodyPreview,body,isRead,hasAttachments,"
    "parentFolderId,isDraft,internetMessageId"
)


def _get_today_filter() -> str:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return f"receivedDateTime ge {today.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def _parse_recipients(recipients: list) -> list[dict]:
    return [
        {
            "name": r.get("emailAddress", {}).get("name", ""),
            "email": r.get("emailAddress", {}).get("address", ""),
        }
        for r in (recipients or [])
    ]


async def _get_headers() -> dict:
    token = await get_valid_access_token()
    if not token:
        raise Exception("Not authenticated")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─── 폴더 관련 ─────────────────────────────────────────────────────────────────

async def fetch_all_folders() -> list[dict]:
    headers = await _get_headers()
    folders = []

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"{GRAPH_BASE}/me/mailFolders?$top=100&includeHiddenFolders=false"
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            folders.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        for folder in folders:
            child_url = f"{GRAPH_BASE}/me/mailFolders/{folder['id']}/childFolders?$top=100"
            while child_url:
                cr = await client.get(child_url, headers=headers)
                if cr.status_code == 200:
                    cdata = cr.json()
                    children = cdata.get("value", [])
                    for ch in children:
                        ch["parentFolderId"] = folder["id"]
                        folders.append(ch)
                    child_url = cdata.get("@odata.nextLink")
                else:
                    break

    return folders


async def save_folders_to_db(folders: list[dict]):
    async with get_session() as session:
        for f in folders:
            await session.execute(
                text("""
                    INSERT INTO sync_folders (folder_id, folder_name, parent_id, mail_count)
                    VALUES (:fid, :fname, :pid, :cnt)
                    ON CONFLICT(folder_id) DO UPDATE SET
                        folder_name = excluded.folder_name,
                        parent_id = excluded.parent_id,
                        mail_count = excluded.mail_count
                """),
                {
                    "fid": f["id"],
                    "fname": f.get("displayName", ""),
                    "pid": f.get("parentFolderId"),
                    "cnt": f.get("totalItemCount", 0),
                },
            )
        await session.commit()


# ─── 메일 파싱 + 저장 ──────────────────────────────────────────────────────────

def _parse_message(msg: dict, folder_id: str = "", folder_name: str = "", my_email: str = "") -> dict:
    sender = msg.get("sender", {}).get("emailAddress", {})
    is_from_me = sender.get("address", "").lower() == my_email.lower() if my_email else False

    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversationId", ""),
        "subject": msg.get("subject", ""),
        "folder_id": folder_id or msg.get("parentFolderId", ""),
        "folder_name": folder_name,
        "sender": sender.get("name", ""),
        "sender_email": sender.get("address", ""),
        "to_recipients": json.dumps(_parse_recipients(msg.get("toRecipients", [])), ensure_ascii=False),
        "cc_recipients": json.dumps(_parse_recipients(msg.get("ccRecipients", [])), ensure_ascii=False),
        "received_at": msg.get("receivedDateTime", ""),
        "body_preview": msg.get("bodyPreview", "")[:200],
        "body": msg.get("body", {}).get("content", ""),
        "is_read": 1 if msg.get("isRead") else 0,
        "has_attachments": 1 if msg.get("hasAttachments") else 0,
        "is_from_me": 1 if is_from_me else 0,
    }


async def upsert_message(session, msg_data: dict):
    await session.execute(
        text("""
            INSERT OR IGNORE INTO messages
                (id, conversation_id, folder_id, folder_name, sender, sender_email,
                 to_recipients, cc_recipients, received_at, body_preview, body,
                 is_read, has_attachments, is_from_me)
            VALUES (:id, :cid, :fid, :fname, :sender, :sender_email,
                    :to_r, :cc_r, :recv, :preview, :body,
                    :is_read, :has_att, :is_from_me)
        """),
        {
            "id": msg_data["id"],
            "cid": msg_data["conversation_id"],
            "fid": msg_data["folder_id"],
            "fname": msg_data["folder_name"],
            "sender": msg_data["sender"],
            "sender_email": msg_data["sender_email"],
            "to_r": msg_data["to_recipients"],
            "cc_r": msg_data["cc_recipients"],
            "recv": msg_data["received_at"],
            "preview": msg_data["body_preview"],
            "body": msg_data["body"],
            "is_read": msg_data["is_read"],
            "has_att": msg_data["has_attachments"],
            "is_from_me": msg_data["is_from_me"],
        },
    )


async def upsert_thread(session, msg_data: dict):
    await session.execute(
        text("""
            INSERT INTO threads (conversation_id, subject, latest_at)
            VALUES (:cid, :subj, :lat)
            ON CONFLICT(conversation_id) DO UPDATE SET
                latest_at = CASE
                    WHEN excluded.latest_at > threads.latest_at
                    THEN excluded.latest_at
                    ELSE threads.latest_at
                END,
                subject = CASE
                    WHEN threads.subject IS NULL OR threads.subject = ''
                    THEN excluded.subject
                    ELSE threads.subject
                END
        """),
        {
            "cid": msg_data["conversation_id"],
            "subj": msg_data.get("subject", ""),
            "lat": msg_data["received_at"],
        },
    )


# ─── Full Sync ─────────────────────────────────────────────────────────────────

async def full_sync_folder(folder_id: str, folder_name: str, my_email: str = "") -> int:
    headers = await _get_headers()
    today_filter = _get_today_filter()
    count = 0

    async with get_session() as session:
        async with httpx.AsyncClient(timeout=60) as client:
            url = (
                f"{GRAPH_BASE}/me/mailFolders/{folder_id}/messages"
                f"?$filter={today_filter}&$top=100&$select={MESSAGE_SELECT}"
            )
            while url:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    break
                data = resp.json()
                messages = data.get("value", [])

                for msg in messages:
                    if msg.get("isDraft"):
                        continue
                    msg_data = _parse_message(msg, folder_id, folder_name, my_email)
                    await upsert_thread(session, msg_data)
                    await upsert_message(session, msg_data)
                    count += 1

                await session.commit()
                url = data.get("@odata.nextLink")

            # delta link 저장
            delta_resp = await client.get(
                f"{GRAPH_BASE}/me/mailFolders/{folder_id}/messages/delta"
                f"?$filter={today_filter}&$select=id",
                headers=headers,
            )
            if delta_resp.status_code == 200:
                delta_data = delta_resp.json()
                delta_link = delta_data.get("@odata.deltaLink", "")
                while not delta_link:
                    next_url = delta_data.get("@odata.nextLink")
                    if not next_url:
                        break
                    nr = await client.get(next_url, headers=headers)
                    delta_data = nr.json()
                    delta_link = delta_data.get("@odata.deltaLink", "")

                if delta_link:
                    await session.execute(
                        text("""
                            INSERT INTO sync_state (folder_id, delta_link, last_sync)
                            VALUES (:fid, :dl, datetime('now'))
                            ON CONFLICT(folder_id) DO UPDATE SET
                                delta_link = excluded.delta_link,
                                last_sync = excluded.last_sync
                        """),
                        {"fid": folder_id, "dl": delta_link},
                    )
            await session.commit()

    return count


# ─── Delta Sync ────────────────────────────────────────────────────────────────

async def delta_sync_folder(folder_id: str, folder_name: str, my_email: str = "") -> list[dict]:
    async with get_session() as session:
        result = await session.execute(
            text("SELECT delta_link FROM sync_state WHERE folder_id = :fid"),
            {"fid": folder_id},
        )
        row = result.mappings().first()

    if not row:
        return []

    delta_link_url = row["delta_link"]
    headers = await _get_headers()
    new_messages = []

    async with httpx.AsyncClient(timeout=60) as client:
        url = delta_link_url
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 410:
                return []
            if resp.status_code != 200:
                break
            data = resp.json()
            messages = data.get("value", [])

            async with get_session() as session:
                for msg in messages:
                    if msg.get("isDraft") or msg.get("@removed"):
                        continue
                    if "receivedDateTime" not in msg:
                        detail = await client.get(
                            f"{GRAPH_BASE}/me/messages/{msg['id']}?$select={MESSAGE_SELECT}",
                            headers=headers,
                        )
                        if detail.status_code != 200:
                            continue
                        msg = detail.json()

                    msg_data = _parse_message(msg, folder_id, folder_name, my_email)
                    await upsert_thread(session, msg_data)
                    await upsert_message(session, msg_data)

                    # Done 쓰레드에 새 메일이 오면 inbox로 복귀
                    t_result = await session.execute(
                        text("SELECT status FROM threads WHERE conversation_id = :cid"),
                        {"cid": msg_data["conversation_id"]},
                    )
                    t_row = t_result.mappings().first()
                    if t_row and t_row["status"] == "done":
                        await session.execute(
                            text("UPDATE threads SET status='inbox', has_new_reply=1 WHERE conversation_id=:cid"),
                            {"cid": msg_data["conversation_id"]},
                        )

                    new_messages.append(msg_data)
                await session.commit()

            delta_link = data.get("@odata.deltaLink", "")
            url = data.get("@odata.nextLink")
            if delta_link:
                async with get_session() as session:
                    await session.execute(
                        text("""
                            UPDATE sync_state SET delta_link=:dl, last_sync=datetime('now')
                            WHERE folder_id=:fid
                        """),
                        {"dl": delta_link, "fid": folder_id},
                    )
                    await session.commit()

    return new_messages


# ─── 전체 쓰레드 메일 조회 (온디맨드) ────────────────────────────────────────────

async def fetch_conversation_messages(conversation_id: str) -> list[dict]:
    headers = await _get_headers()
    messages = []

    async with httpx.AsyncClient(timeout=30) as client:
        # $orderby와 $filter(conversationId)를 동시에 사용하면 Graph가 400을 반환하므로
        # $orderby를 제거하고 Python에서 직접 정렬한다.
        url = (
            f"{GRAPH_BASE}/me/messages"
            f"?$filter=conversationId eq '{conversation_id}'"
            f"&$select={MESSAGE_SELECT}&$top=100"
        )
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "fetch_conversation_messages failed [%s]: %s",
                    resp.status_code,
                    resp.text[:300],
                )
                break
            data = resp.json()
            messages.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

    messages.sort(key=lambda m: m.get("receivedDateTime", ""))
    return messages


# ─── 첨부파일 조회 ─────────────────────────────────────────────────────────────

async def fetch_message_attachments(message_id: str) -> dict:
    """
    메시지의 첨부파일 목록을 가져온다.
    반환: {
        "inline": [{"content_id": str, "content_type": str, "content_bytes": str}],
        "files":  [{"id": str, "name": str, "size": int, "content_type": str}]
    }
    """
    headers = await _get_headers()
    inline = []
    files = []

    async with httpx.AsyncClient(timeout=30) as client:
        # $select에 contentId를 포함하면 Graph API가 400 반환:
        # "Could not find a property named 'contentId' on type 'microsoft.graph.attachment'"
        # contentId는 fileAttachment 파생 타입 전용이므로 $select 없이 요청해야 반환됨.
        url = f"{GRAPH_BASE}/me/messages/{message_id}/attachments"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "fetch_message_attachments failed [%s] msg=%s: %s",
                resp.status_code, message_id[:20], resp.text[:200],
            )
            return {"inline": [], "files": []}

        attachments = resp.json().get("value", [])
        logger.info(
            "fetch_message_attachments msg=%s → %d attachments",
            message_id[:30], len(attachments),
        )
        for att in attachments:
            cid = att.get("contentId", "")
            has_cid = bool(cid)
            logger.info(
                "  att name=%s isInline=%s contentId=%s",
                att.get("name"), att.get("isInline"), cid[:40] if cid else None,
            )
            if has_cid:
                # contentId가 있으면 HTML body의 cid: 참조 대상이므로 프록시 URL용으로 저장.
                # contentBytes 유무와 관계없이 att_id만 있으면 프록시 다운로드 가능.
                inline.append({
                    "att_id": att["id"],
                    "content_id": cid.strip("<>"),
                    "content_type": att.get("contentType", "image/png"),
                })
            else:
                files.append({
                    "id": att["id"],
                    "name": att.get("name", "첨부파일"),
                    "size": att.get("size", 0),
                    "content_type": att.get("contentType", "application/octet-stream"),
                })

    return {"inline": inline, "files": files}


async def download_attachment_bytes(message_id: str, attachment_id: str) -> tuple[bytes, str, str]:
    """
    첨부파일 바이너리 데이터 반환.
    Returns: (content_bytes, content_type, filename)
    """
    headers = await _get_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        # $select에 contentBytes 포함 시 Graph API 400 오류 발생 (fileAttachment 전용 속성)
        meta_resp = await client.get(
            f"{GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}",
            headers=headers,
        )
        meta_resp.raise_for_status()
        data = meta_resp.json()
        import base64
        content_bytes = base64.b64decode(data.get("contentBytes", ""))
        content_type = data.get("contentType", "application/octet-stream")
        filename = data.get("name", "attachment")
        return content_bytes, content_type, filename


# ─── 회신 ──────────────────────────────────────────────────────────────────────

async def send_reply(
    message_id: str,
    body: str,
    reply_type: str,
    to_recipients: list[dict],
    cc_recipients: list[dict],
    bcc_recipients: list[dict],
) -> dict:
    headers = await _get_headers()

    def fmt_recipients(recs):
        return [{"emailAddress": {"name": r["name"], "address": r["email"]}} for r in recs]

    if reply_type == "forward":
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{GRAPH_BASE}/me/messages/{message_id}/createForward",
                headers=headers,
                json={},
            )
            r.raise_for_status()
            create_resp_data = r.json()
            draft_id = create_resp_data["id"]

            patch_data = {
                "toRecipients": fmt_recipients(to_recipients),
                "ccRecipients": fmt_recipients(cc_recipients),
                "bccRecipients": fmt_recipients(bcc_recipients),
                "body": {"contentType": "HTML", "content": body},
            }
            await client.patch(f"{GRAPH_BASE}/me/messages/{draft_id}", headers=headers, json=patch_data)
            send_r = await client.post(f"{GRAPH_BASE}/me/messages/{draft_id}/send", headers=headers)
            send_r.raise_for_status()
            return create_resp_data
    else:
        endpoint = (
            f"{GRAPH_BASE}/me/messages/{message_id}/createReply"
            if reply_type == "reply"
            else f"{GRAPH_BASE}/me/messages/{message_id}/createReplyAll"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(endpoint, headers=headers, json={})
            r.raise_for_status()
            draft = r.json()
            draft_id = draft["id"]

            patch_data: dict = {"body": {"contentType": "HTML", "content": body}}
            if reply_type == "reply":
                patch_data["toRecipients"] = fmt_recipients(to_recipients)
            if cc_recipients:
                patch_data["ccRecipients"] = fmt_recipients(cc_recipients)
            if bcc_recipients:
                patch_data["bccRecipients"] = fmt_recipients(bcc_recipients)

            await client.patch(f"{GRAPH_BASE}/me/messages/{draft_id}", headers=headers, json=patch_data)
            send_r = await client.post(f"{GRAPH_BASE}/me/messages/{draft_id}/send", headers=headers)
            send_r.raise_for_status()
            return draft


# ─── 폴더 이동 ─────────────────────────────────────────────────────────────────

async def move_conversation(conversation_id: str, destination_folder_id: str) -> int:
    headers = await _get_headers()
    messages = []
    moved = 0

    async with httpx.AsyncClient(timeout=60) as client:
        url = (
            f"{GRAPH_BASE}/me/messages"
            f"?$filter=conversationId eq '{conversation_id}'"
            f"&$select=id,parentFolderId&$top=100"
        )
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                break
            data = resp.json()
            messages.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        for msg in messages:
            if msg.get("parentFolderId") != destination_folder_id:
                r = await client.post(
                    f"{GRAPH_BASE}/me/messages/{msg['id']}/move",
                    headers=headers,
                    json={"destinationId": destination_folder_id},
                )
                if r.status_code == 201:
                    moved += 1

    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_name FROM sync_folders WHERE folder_id = :fid"),
            {"fid": destination_folder_id},
        )
        row = result.mappings().first()
        dest_name = row["folder_name"] if row else ""

        await session.execute(
            text("UPDATE messages SET folder_id=:fid, folder_name=:fname WHERE conversation_id=:cid"),
            {"fid": destination_folder_id, "fname": dest_name, "cid": conversation_id},
        )
        await session.commit()

    return moved


# ─── 수신자 검색 ───────────────────────────────────────────────────────────────

async def search_people(query: str) -> list[dict]:
    headers = await _get_headers()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/me/people",
            headers=headers,
            params={"$search": query, "$top": 10, "$select": "displayName,scoredEmailAddresses"},
        )
        if resp.status_code != 200:
            return []
        people = resp.json().get("value", [])
        result = []
        for p in people:
            for email_obj in p.get("scoredEmailAddresses", []):
                addr = email_obj.get("address", "")
                if addr and "@" in addr:
                    result.append({"name": p.get("displayName", ""), "email": addr})
                    break
        return result
