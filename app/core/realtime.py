"""WebSocket 접속자에게 이벤트를 나눠 주는 허브.

API 처리는 별도 스레드(동기 함수)에서, WebSocket은 asyncio 이벤트 루프에서 돌기 때문에
publish()는 어느 스레드에서 불러도 안전하도록 call_soon_threadsafe로 각 접속자의 큐에 넣는다.

[가정] 서버 프로세스 1개 기준이다. 여러 프로세스(워커)로 실행하면 Redis 같은 공용 채널이 필요하다.
"""
import asyncio
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Subscriber:
        sub = Subscriber(loop=asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.loop.call_soon_threadsafe(self._put, sub, event)
            except RuntimeError:  # 접속자의 이벤트 루프가 이미 닫힘
                self.unsubscribe(sub)

    @staticmethod
    def _put(sub: Subscriber, event: dict) -> None:
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:  # 너무 느린 접속자: 오래된 이벤트를 버리지 않고 새 이벤트를 버린다
            logger.warning("WebSocket 접속자 큐가 가득 차 이벤트를 버림: %s", event.get("type"))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


hub = EventHub()
