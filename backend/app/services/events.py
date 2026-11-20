"""Thread-safe project event bus used by SSE and the asynchronous job runner."""
from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    project_id: str
    type: str
    payload: dict[str, Any]
    seq: int = 0
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        import json

        body = {
            "seq": self.seq,
            "type": self.type,
            "project_id": self.project_id,
            "payload": self.payload,
            "ts": self.ts,
        }
        return f"id: {self.seq}\nevent: {self.type}\ndata: {json.dumps(body, default=str)}\n\n"


class EventBus:
    def __init__(self, history_size: int = 500) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._history: dict[str, deque[Event]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        self._lock = threading.Lock()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def publish(self, project_id: str, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(
            project_id=project_id, type=event_type, payload=payload or {}, seq=self._next_seq()
        )
        with self._lock:
            self._history[project_id].append(event)
            queues = list(self._subscribers.get(project_id, ()))

        def deliver() -> None:
            for queue in queues:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:  # pragma: no cover - slow consumer
                    pass

        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(deliver)
        else:  # unit tests / sync contexts
            deliver()
        return event

    def history(self, project_id: str, since: int = 0) -> list[Event]:
        with self._lock:
            return [e for e in self._history[project_id] if e.seq > since]

    def subscribe(self, project_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers[project_id].add(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers[project_id].discard(queue)


bus = EventBus()
