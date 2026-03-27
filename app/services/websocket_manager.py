"""
WebSocket 연결 관리자 — 멀티유저 지원.
- 사용자별 연결 관리 (user_id → [WebSocket, ...])
- 프로젝트별 이벤트 라우팅
- broadcast_to_user: 특정 사용자에게만 메시지 전송
- broadcast_to_project: 특정 프로젝트 멤버에게 메시지 전송
- broadcast: 모든 연결에 메시지 전송 (기존 호환성 유지)
"""
import json
import logging
from collections import defaultdict
from fastapi import WebSocket

logger = logging.getLogger("autoreply.websocket")


class WebSocketManager:
    def __init__(self):
        # 전체 연결 목록
        self.active_connections: list[WebSocket] = []
        # user_id → [WebSocket, ...]
        self.user_connections: dict[str, list[WebSocket]] = defaultdict(list)
        # WebSocket → user_id (역방향 매핑)
        self.connection_user: dict[WebSocket, str] = {}
        # WebSocket → project_id (현재 활성 프로젝트)
        self.connection_project: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, user_id: str = None, project_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if user_id:
            self.user_connections[user_id].append(websocket)
            self.connection_user[websocket] = user_id
        if project_id:
            self.connection_project[websocket] = project_id
        logger.debug("WebSocket connected: user=%s project=%s total=%d", user_id, project_id, len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

        user_id = self.connection_user.pop(websocket, None)
        if user_id and websocket in self.user_connections.get(user_id, []):
            self.user_connections[user_id].remove(websocket)

        self.connection_project.pop(websocket, None)
        logger.debug("WebSocket disconnected: user=%s total=%d", user_id, len(self.active_connections))

    async def broadcast(self, message: dict):
        """모든 연결에 메시지 전송."""
        data = json.dumps(message, ensure_ascii=False)
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_text(data)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)

    async def broadcast_to_user(self, user_id: str, message: dict):
        """특정 사용자의 모든 연결에 메시지 전송."""
        data = json.dumps(message, ensure_ascii=False)
        dead = []
        for conn in self.user_connections.get(user_id, []):
            try:
                await conn.send_text(data)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)

    async def broadcast_to_project(self, project_id: str, message: dict):
        """특정 프로젝트의 모든 멤버에게 메시지 전송."""
        data = json.dumps(message, ensure_ascii=False)
        dead = []
        for conn, pid in list(self.connection_project.items()):
            if pid == project_id:
                try:
                    await conn.send_text(data)
                except Exception:
                    dead.append(conn)
        for conn in dead:
            self.disconnect(conn)

    def set_project(self, websocket: WebSocket, project_id: str):
        """WebSocket 연결의 활성 프로젝트 설정."""
        self.connection_project[websocket] = project_id


ws_manager = WebSocketManager()
