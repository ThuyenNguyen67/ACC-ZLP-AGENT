from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal


RunStatus = Literal["pending", "running", "complete", "error"]


@dataclass
class RunRecord:
    run_id: str
    workdir: Path
    status: RunStatus = "pending"
    tolerance: int = 0
    payment_file: Path | None = None
    statement_files: list[Path] = field(default_factory=list)
    cashbook_file: Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class RunStore:
    def __init__(self, base_workdir: str | Path):
        self.base_workdir = Path(base_workdir)
        self.base_workdir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def create(
        self,
        *,
        tolerance: int,
        payment_file: Path,
        statement_files: list[Path],
        cashbook_file: Path,
    ) -> RunRecord:
        run_id = uuid.uuid4().hex
        workdir = self.base_workdir / run_id
        workdir.mkdir(parents=True, exist_ok=False)
        record = RunRecord(
            run_id=run_id,
            workdir=workdir,
            status="running",
            tolerance=tolerance,
            payment_file=payment_file,
            statement_files=statement_files,
            cashbook_file=cashbook_file,
        )
        with self._lock:
            self._runs[run_id] = record
        self.add_event(run_id, "status", {"status": "Files received"})
        return record

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id]

    def add_event(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.events.append({"event": event, "data": data})
            self._condition.notify_all()

    def complete(self, run_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.status = "complete"
            record.result = result
            record.events.append({"event": "complete", "data": result})
            self._condition.notify_all()

    def fail(self, run_id: str, message: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.status = "error"
            record.error = message
            record.events.append({"event": "error", "data": {"error": message}})
            self._condition.notify_all()

    def sse(self, run_id: str) -> str:
        record = self.get(run_id)
        chunks = []
        for item in record.events:
            chunks.append(f"event: {item['event']}\n")
            chunks.append(f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n")
        return "".join(chunks)

    def iter_sse(self, run_id: str):
        index = 0
        while True:
            with self._condition:
                record = self._runs[run_id]
                while index >= len(record.events) and record.status not in {"complete", "error"}:
                    self._condition.wait(timeout=0.5)
                events = record.events[index:]
                index = len(record.events)
                terminal = record.status in {"complete", "error"}

            for item in events:
                yield f"event: {item['event']}\n"
                yield f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n"

            if terminal and not events:
                return
            if terminal and index >= len(self.get(run_id).events):
                return


Runner = Callable[[str, RunStore], None]
