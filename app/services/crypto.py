"""
AES-256-GCM 암복호화 서비스.
암호화 대상: access_token, refresh_token, Slack Webhook URL
키: ENCRYPTION_KEY (base64 인코딩된 32 bytes) — setup.sh에서 자동 생성
"""
import os
import base64
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("autoreply.crypto")


def encrypt(plaintext: str, key: bytes) -> str:
    """평문 → AES-256-GCM 암호문 (base64 인코딩)."""
    if not plaintext:
        return ""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("utf-8")


def decrypt(encrypted: str, key: bytes) -> str:
    """AES-256-GCM 암호문 → 평문."""
    if not encrypted:
        return ""
    try:
        data = base64.b64decode(encrypted)
        nonce, ct = data[:12], data[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except Exception as exc:
        logger.error("Decryption failed: %s", exc)
        return ""


def get_key() -> bytes:
    """설정에서 암호화 키 로드."""
    from app.config import settings
    return settings.encryption_key_bytes
