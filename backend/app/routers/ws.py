"""실시간 이벤트 WebSocket.

접속: ws://<서버>/ws/events?token=<관리자 토큰>
브라우저 WebSocket은 헤더를 붙일 수 없어 토큰을 쿼리로 받는다. [가정] 로그인 방식이 정해지면 바꾼다.

서버 → 클라이언트 메시지
- {"type": "connected", ...}  접속 직후 1회
- {"type": "ping", ...}       30초마다 (연결 유지 확인용, 응답 불필요)
- 그 외 이벤트                 app/services/realtime_events.py 참고
"""
import asyncio
import hmac

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from app.core.config import Settings, get_settings
from app.core.realtime import hub
from app.db.base import utcnow

router = APIRouter()

PING_INTERVAL_SECONDS = 30


def _now() -> str:
    return utcnow().isoformat() + "Z"


@router.websocket("/ws/events")
async def events(websocket: WebSocket, token: str = Query(default=""), settings: Settings = Depends(get_settings)):
    admin_id = next((a for t, a in settings.admin_token_map.items() if hmac.compare_digest(t, token)), None)
    if admin_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="관리자 토큰이 필요합니다.")
        return

    await websocket.accept()
    sub = hub.subscribe()
    await websocket.send_json({"type": "connected", "at": _now(), "data": {"admin_id": admin_id}})

    async def wait_for_disconnect() -> None:
        try:
            while True:
                await websocket.receive_text()  # 클라이언트 메시지는 쓰지 않음 (끊김 감지용)
        except WebSocketDisconnect:
            pass

    disconnect = asyncio.create_task(wait_for_disconnect())
    try:
        while not disconnect.done():
            next_event = asyncio.create_task(sub.queue.get())
            done, _ = await asyncio.wait({next_event, disconnect}, timeout=PING_INTERVAL_SECONDS,
                                         return_when=asyncio.FIRST_COMPLETED)
            if next_event in done:
                await websocket.send_json(next_event.result())
            else:
                next_event.cancel()
                if not disconnect.done():
                    await websocket.send_json({"type": "ping", "at": _now(), "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(sub)
        disconnect.cancel()
