import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.database import get_session
from app.middleware import require_login
from app.models.schemas import FolderSyncRequest
from app.services.auth_service import get_valid_access_token
from app.services.outlook import fetch_all_folders, save_folders_to_db

logger = logging.getLogger("autoreply.folders")

router = APIRouter(prefix="/api/folders", tags=["folders"])


@router.get("")
async def get_folders(user: dict = Depends(require_login)):
    # DB에 폴더가 있으면 토큰 없이 바로 반환 (캐시 우선)
    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_id, folder_name, parent_id, is_synced, is_team_visible, mail_count FROM sync_folders")
        )
        rows = result.mappings().all()

    if rows:
        return {"folders": [dict(r) for r in rows]}

    # DB가 비어있으면 Graph API에서 새로 가져옴 (토큰 필요)
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    folders = await fetch_all_folders()
    await save_folders_to_db(folders)
    async with get_session() as session:
        result = await session.execute(
            text("SELECT folder_id, folder_name, parent_id, is_synced, is_team_visible, mail_count FROM sync_folders")
        )
        rows = result.mappings().all()

    return {"folders": [dict(r) for r in rows]}


@router.post("/refresh")
async def refresh_folders(user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    folders = await fetch_all_folders()
    await save_folders_to_db(folders)
    return {"count": len(folders)}


@router.patch("/{folder_id}/sync")
async def set_folder_sync(folder_id: str, body: FolderSyncRequest, user: dict = Depends(require_login)):
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    async with get_session() as session:
        await session.execute(
            text("UPDATE sync_folders SET is_synced=:val WHERE folder_id=:fid"),
            {"val": 1 if body.is_synced else 0, "fid": folder_id},
        )
        await session.commit()

    return {"folder_id": folder_id, "is_synced": body.is_synced}


@router.patch("/{folder_id}/visibility")
async def set_folder_visibility(folder_id: str, body: dict, user: dict = Depends(require_login)):
    """폴더 팀 공개/비공개 토글."""
    is_visible = body.get("is_team_visible", False)
    async with get_session() as session:
        await session.execute(
            text("UPDATE sync_folders SET is_team_visible=:val WHERE folder_id=:fid"),
            {"val": 1 if is_visible else 0, "fid": folder_id},
        )
        await session.commit()
    return {"folder_id": folder_id, "is_team_visible": is_visible}


@router.post("/save-selection")
async def save_folder_selection(body: dict, user: dict = Depends(require_login)):
    """선택된 폴더 목록을 일괄 저장."""
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(status_code=503, detail="Outlook이 연동되지 않았습니다.")

    selected_ids = set(body.get("selected_folder_ids", []))

    async with get_session() as session:
        await session.execute(text("UPDATE sync_folders SET is_synced=0"))
        for fid in selected_ids:
            await session.execute(
                text("UPDATE sync_folders SET is_synced=1 WHERE folder_id=:fid"),
                {"fid": fid},
            )
        await session.commit()

    return {"saved": len(selected_ids)}
