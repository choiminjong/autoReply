import os
import base64
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Microsoft Graph API
    client_id: str = "c82cb0ff-6093-4bcd-a654-18942bdf5e8d"
    tenant_id: str = "558a3344-3004-4f39-816f-4dfa342d9d5f"
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8000/api/auth/callback"
    graph_scopes: str = "offline_access Mail.Read Mail.Read.Shared Mail.ReadWrite Mail.Send User.Read People.Read"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/emails.db"
    auto_migrate: bool = True

    # Security
    encryption_key: str = ""
    secret_key: str = "change-me-to-random-string"

    # App
    host: str = "0.0.0.0"
    port: int = 8000
    poll_interval_seconds: int = 180

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def token_url(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"

    @property
    def auth_url(self) -> str:
        return f"{self.authority}/oauth2/v2.0/authorize"

    @property
    def scope(self) -> str:
        return self.graph_scopes

    @property
    def encryption_key_bytes(self) -> bytes:
        if not self.encryption_key:
            raise ValueError("ENCRYPTION_KEY is not set in .env. Run setup.sh to generate it.")
        return base64.b64decode(self.encryption_key)

    class Config:
        env_file = ".env"


settings = Settings()
