import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import text

from app.database import get_session
from app.middleware import require_login
from app.services.auth_service import get_valid_access_token
from app.services.outlook import delta_sync_folder, full_sync_folder, search_people
from app.services.websocket_manager import ws_manager
import httpx

logger = logging.getLogger("autoreply.sync")

router = APIRouter(prefix="/api", tags=["sync"])

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def _get_my_email(token: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("mail") or data.get("userPrincipalName", "")
    return ""


async def _do_full_sync():
    token = await get_valid_access_token()
    if not token:
        return 0

    my_email = await _get_my_email(token)

    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_id, folder_name FROM sync_folders WHERE is_synced=1")
        )
        rows = result.mappings().all()

    total = 0
    for r in rows:
        count = await full_sync_folder(r["folder_id"], r["folder_name"], my_email)
        total += count

    await ws_manager.broadcast({"type": "sync_complete", "data": {"count": total}})
    return total


async def _do_delta_sync():
    token = await get_valid_access_token()
    if not token:
        return []

    my_email = await _get_my_email(token)

    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_id, folder_name FROM sync_folders WHERE is_synced=1")
        )
        rows = result.mappings().all()

    all_new = []
    for r in rows:
        new_msgs = await delta_sync_folder(r["folder_id"], r["folder_name"], my_email)
        all_new.extend(new_msgs)

    if all_new:
        await ws_manager.broadcast({
            "type": "new_mail",
            "data": {"count": len(all_new)},
        })
    return all_new


@router.post("/emails/sync")
async def manual_sync(background_tasks: BackgroundTasks, user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    async with get_session() as session:
        result = await session.execute(text("SELECT folder_id FROM sync_state LIMIT 1"))
        has_delta = result.mappings().first() is not None

    if has_delta:
        background_tasks.add_task(_do_delta_sync)
        return {"mode": "delta", "message": "Delta sync started"}
    else:
        background_tasks.add_task(_do_full_sync)
        return {"mode": "full", "message": "Full sync started"}


@router.post("/emails/full-sync")
async def force_full_sync(background_tasks: BackgroundTasks, user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    background_tasks.add_task(_do_full_sync)
    return {"mode": "full", "message": "Full sync started"}


@router.get("/sync/status")
async def sync_status(user: dict = Depends(require_login)):
    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_id, last_sync FROM sync_state ORDER BY last_sync DESC LIMIT 1")
        )
        row = result.mappings().first()
        last_sync = row["last_sync"] if row else None

        count_result = await session.execute(
            text("SELECT COUNT(*) as cnt FROM sync_folders WHERE is_synced=1")
        )
        synced_count = count_result.mappings().first()["cnt"]

    return {"last_sync": last_sync, "synced_folder_count": synced_count}


@router.get("/people/search")
async def people_search(q: str = "", user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    if not q or len(q) < 2:
        return {"people": []}

    people = await search_people(q)
    return {"people": people}
