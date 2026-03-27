"""
로컬 로그인 / 회원가입 / 로그아웃 API.
Microsoft OAuth 콜백 및 토큰 갱신도 이 라우터에서 처리.
"""
import logging
import uuid
from datetime import datetime, timedelta

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.database import get_session
from app.middleware import require_login
from app.services.crypto import decrypt, encrypt, get_key
from app.services.session_service import COOKIE_NAME, create_session, delete_session

# 순환참조 방지: 런타임에 import
async def _get_active_scope() -> str:
    from app.routers.admin import get_active_scope
    return await get_active_scope()

logger = logging.getLogger("autoreply.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── 요청 스키마 ───────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    display_name: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _validate_password(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 최소 8자 이상이어야 합니다.")


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/register")
async def register(body: RegisterRequest):
    """회원가입. 최초 가입자는 admin 역할 자동 부여."""
    _validate_password(body.password)

    async with get_session() as session:
        # 이메일 중복 체크
        result = await session.execute(
            text("SELECT user_id FROM users WHERE email = :email"),
            {"email": body.email.lower()},
        )
        if result.mappings().first():
            raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")

        # 최초 가입자 = admin
        count_result = await session.execute(text("SELECT COUNT(*) as cnt FROM users"))
        cnt = count_result.mappings().first()["cnt"]
        role = "admin" if cnt == 0 else "user"

        user_id = str(uuid.uuid4())
        password_hash = _hash_password(body.password)
        now = datetime.utcnow().isoformat()

        await session.execute(
            text("""
                INSERT INTO users (user_id, email, display_name, password_hash, role, is_active, created_at)
                VALUES (:uid, :email, :name, :hash, :role, 1, :now)
            """),
            {
                "uid": user_id,
                "email": body.email.lower(),
                "name": body.display_name,
                "hash": password_hash,
                "role": role,
                "now": now,
            },
        )
        await session.commit()

    logger.info("User registered: %s (role=%s)", body.email, role)
    return {"message": "회원가입이 완료되었습니다.", "role": role}


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    """로컬 로그인 → 세션 쿠키 발급."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT user_id, password_hash, role, is_active FROM users WHERE email = :email"),
            {"email": body.email.lower()},
        )
        user = result.mappings().first()

    if not user:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    if not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    session_id, csrf_token = await create_session(user["user_id"])

    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )

    logger.info("User logged in: %s", body.email)
    return {"message": "로그인 성공", "csrf_token": csrf_token, "role": user["role"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """로그아웃 → 세션 삭제 + 쿠키 제거."""
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        await delete_session(session_id)
    response.delete_cookie(COOKIE_NAME)
    return {"message": "로그아웃 되었습니다."}


@router.get("/me")
async def get_me(user: dict = Depends(require_login)):
    """현재 로그인된 사용자 정보 반환."""
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


# ── Microsoft OAuth 콜백 ──────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@router.get("/callback")
async def ms_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """Microsoft OAuth 콜백 — 토큰 교환 후 AES-256-GCM 암호화 저장."""
    print("=" * 60)
    print("[CALLBACK] /api/auth/callback 호출됨")
    print(f"[CALLBACK] 전체 URL : {request.url}")
    print(f"[CALLBACK] code     : {'있음 (길이=' + str(len(code)) + ')' if code else '없음'}")
    print(f"[CALLBACK] state    : {state}")
    print(f"[CALLBACK] error    : {error or '없음'}")
    print(f"[CALLBACK] error_desc: {error_description or '없음'}")
    print("=" * 60)

    if error:
        print(f"[CALLBACK] ❌ OAuth 에러 반환됨: {error} — {error_description}")
        raise HTTPException(status_code=400, detail=f"{error}: {error_description}")
    if not code:
        print("[CALLBACK] ❌ 인증 코드 없음")
        raise HTTPException(status_code=400, detail="인증 코드가 없습니다.")

    user_id = state
    print(f"[CALLBACK] ✅ 코드 수신 완료, 토큰 교환 시작 (user_id={user_id})")

    active_scope = await _get_active_scope()
    payload: dict = {
        "client_id": settings.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "scope": active_scope,
    }
    if settings.client_secret:
        payload["client_secret"] = settings.client_secret
        print("[CALLBACK] client_secret 포함됨")
    else:
        print("[CALLBACK] client_secret 없음 (공개 클라이언트 모드)")

    print(f"[CALLBACK] token_url : {settings.token_url}")
    print(f"[CALLBACK] redirect  : {settings.redirect_uri}")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.token_url, data=payload)
        print(f"[CALLBACK] 토큰 교환 응답 status: {resp.status_code}")
        data = resp.json()
        if resp.status_code != 200:
            print(f"[CALLBACK] ❌ 토큰 교환 실패: {data}")
            raise HTTPException(status_code=400, detail=f"토큰 교환 실패: {data}")

        access_token = data["access_token"]
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", 3600)
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in - 60)).isoformat()
        print(f"[CALLBACK] ✅ 토큰 교환 성공 (expires_in={expires_in}s, refresh_token={'있음' if refresh_token else '없음'})")

        me_resp = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        print(f"[CALLBACK] Graph /me 응답 status: {me_resp.status_code}")
        ms_user_id = ""
        ms_email = ""
        if me_resp.status_code == 200:
            me_data = me_resp.json()
            ms_user_id = me_data.get("id", "")
            ms_email = me_data.get("mail") or me_data.get("userPrincipalName", "")
            print(f"[CALLBACK] ms_user_id : {ms_user_id}")
            print(f"[CALLBACK] ms_email   : {ms_email}")
        else:
            print(f"[CALLBACK] ⚠️  Graph /me 실패: {me_resp.text}")

    key = get_key()
    enc_access = encrypt(access_token, key)
    enc_refresh = encrypt(refresh_token, key)

    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO outlook_tokens
                    (user_id, ms_user_id, ms_email, access_token, refresh_token, expires_at, connected_at)
                VALUES (:uid, :ms_uid, :ms_email, :at, :rt, :exp, :now)
                ON CONFLICT(user_id) DO UPDATE SET
                    ms_user_id = excluded.ms_user_id,
                    ms_email = excluded.ms_email,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    connected_at = excluded.connected_at
            """),
            {
                "uid": user_id,
                "ms_uid": ms_user_id,
                "ms_email": ms_email,
                "at": enc_access,
                "rt": enc_refresh,
                "exp": expires_at,
                "now": datetime.utcnow().isoformat(),
            },
        )
        await session.commit()

    # 레거시 auth_tokens 호환 유지
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO auth_tokens (id, access_token, refresh_token, expires_at)
                VALUES (1, :at, :rt, :exp)
                ON CONFLICT(id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at
            """),
            {"at": access_token, "rt": refresh_token, "exp": expires_at},
        )
        await session.commit()

    print(f"[CALLBACK] ✅ DB 저장 완료 — user_id={user_id}, ms_email={ms_email}")
    print("=" * 60)
    logger.info("Outlook connected via /api/auth/callback: user_id=%s ms_email=%s", user_id, ms_email)
    return RedirectResponse(url="/")


@router.post("/token/refresh")
async def token_refresh(user: dict = Depends(require_login)):
    """Microsoft Graph API 액세스 토큰 수동 갱신."""
    async with get_session() as session:
        result = await session.execute(
            text("SELECT refresh_token FROM outlook_tokens WHERE user_id = :uid"),
            {"uid": user["user_id"]},
        )
        row = result.mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Outlook이 연동되지 않았습니다.")

    key = get_key()
    try:
        raw_refresh = decrypt(row["refresh_token"], key)
    except Exception:
        raise HTTPException(status_code=500, detail="토큰 복호화 실패")

    payload: dict = {
        "client_id": settings.client_id,
        "grant_type": "refresh_token",
        "refresh_token": raw_refresh,
        "scope": settings.scope,
    }
    if settings.client_secret:
        payload["client_secret"] = settings.client_secret

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(settings.token_url, data=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"토큰 갱신 실패: {resp.text}")
        data = resp.json()

    access_token = data["access_token"]
    new_refresh = data.get("refresh_token", raw_refresh)
    expires_in = data.get("expires_in", 3600)
    expires_at = (datetime.utcnow() + timedelta(seconds=expires_in - 60)).isoformat()

    enc_access = encrypt(access_token, key)
    enc_refresh = encrypt(new_refresh, key)

    async with get_session() as session:
        await session.execute(
            text("""
                UPDATE outlook_tokens
                SET access_token = :at, refresh_token = :rt, expires_at = :exp
                WHERE user_id = :uid
            """),
            {"at": enc_access, "rt": enc_refresh, "exp": expires_at, "uid": user["user_id"]},
        )
        await session.commit()

    logger.info("Token refreshed for user_id=%s", user["user_id"])
    return {"message": "토큰이 갱신되었습니다.", "expires_at": expires_at}
