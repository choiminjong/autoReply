from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class EmailStatus(str, Enum):
    INBOX = "inbox"
    AUTO_REPLY = "auto_reply"
    MANUAL = "manual"
    DONE = "done"


class Recipient(BaseModel):
    name: str
    email: str


class AttachmentSchema(BaseModel):
    id: str
    name: str
    size: int
    content_type: str
    is_inline: bool


class MessageSchema(BaseModel):
    id: str
    conversation_id: str
    folder_id: Optional[str] = ""
    folder_name: Optional[str] = ""
    sender: str
    sender_email: str
    to_recipients: list[Recipient] = []
    cc_recipients: list[Recipient] = []
    received_at: str
    body_preview: str
    body: str
    is_read: bool
    has_attachments: bool
    is_from_me: bool
    attachments: list[AttachmentSchema] = []


class ThreadSchema(BaseModel):
    conversation_id: str
    subject: str
    status: EmailStatus
    primary_folder: str = ""
    has_folder_mismatch: bool = False
    latest_at: str
    has_new_reply: bool
    message_count: int
    messages: list[MessageSchema] = []


class ThreadListItem(BaseModel):
    conversation_id: str
    subject: str
    status: EmailStatus
    primary_folder: str = ""
    has_folder_mismatch: bool = False
    latest_at: str
    has_new_reply: bool
    message_count: int
    latest_sender: str = ""
    latest_sender_email: str = ""
    body_preview: str = ""
    has_attachments: bool = False


class ReplyRequest(BaseModel):
    body: str
    reply_type: str = "reply"  # reply / replyAll / forward
    to_recipients: list[Recipient]
    cc_recipients: list[Recipient] = []
    bcc_recipients: list[Recipient] = []


class FolderMoveRequest(BaseModel):
    destination_folder_id: str


class StatusUpdateRequest(BaseModel):
    status: EmailStatus


class FolderSyncRequest(BaseModel):
    is_synced: bool


class UserProfile(BaseModel):
    display_name: str
    email: str
    photo_url: Optional[str] = None


class SyncFolder(BaseModel):
    folder_id: str
    folder_name: str
    parent_id: Optional[str] = None
    is_synced: bool
    mail_count: int
    children: list[SyncFolder] = []


class WSMessage(BaseModel):
    type: str  # new_mail / status_change / sync_complete / new_reply
    data: dict = {}
