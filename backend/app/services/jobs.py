"""Background job runner with per-project event streaming and cancellation."""
from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.db import SessionLocal
from app.services.events import bus


@dataclass
class JobContext:
    id: str
    project_id: str
    kind: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    metadata: dict[str, Any] = field(default_factory=dict)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        bus.publish(self.project_id, event_type, {"job_id": self.id, **(payload or {})})

    def log(self, line: str) -> None:
        self.publish("log", {"line": line})


@dataclass
class JobRecord:
    id: str
    project_id: str
    kind: str
    status: str = "queued"
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    context: JobContext | None = None


class JobRunner:
    def __init__(self, max_workers: int = 3) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tfagent")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        project_id: str,
        kind: str,
        fn: Callable[[JobContext, Any], dict[str, Any] | None],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        context = JobContext(
            id=job_id, project_id=project_id, kind=kind, metadata=metadata or {}
        )
        record = JobRecord(id=job_id, project_id=project_id, kind=kind, context=context)
        with self._lock:
            self._jobs[job_id] = record

        def runner() -> None:
            db = SessionLocal()
            record.status = "running"
            context.publish("job", {"status": "started", "kind": kind})
            try:
                result = fn(context, db) or {}
                db.commit()
                record.status = "succeeded"
                record.result = result
                context.publish("job", {"status": "succeeded", "kind": kind, "result": result})
            except Exception as exc:  # noqa: BLE001 - job boundary
                db.rollback()
                record.status = "cancelled" if context.cancel_event.is_set() else "failed"
                record.error = f"{exc}"
                context.publish(
                    "job",
                    {
                        "status": record.status,
                        "kind": kind,
                        "error": str(exc)[:2000],
                    },
                )
                if not context.cancel_event.is_set():
                    context.log(traceback.format_exc()[-4000:])
            finally:
                db.close()

        self._executor.submit(runner)
        return job_id

    def record(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
        if record and record.context:
            record.context.cancel_event.set()
            record.status = "cancelling"
            return True
        return False

    def cancel_project(self, project_id: str) -> int:
        count = 0
        with self._lock:
            records = [
                r
                for r in self._jobs.values()
                if r.project_id == project_id and r.status in ("queued", "running", "cancelling")
            ]
        for record in records:
            if self.cancel(record.id):
                count += 1
        return count


jobs = JobRunner()


def job_status(job_id: str) -> dict[str, Any] | None:
    record = jobs.record(job_id)
    if not record:
        return None
    return {
        "id": record.id,
        "project_id": record.project_id,
        "kind": record.kind,
        "status": record.status,
        "error": record.error,
        "result": record.result,
        "created_at": record.created_at,
    }
