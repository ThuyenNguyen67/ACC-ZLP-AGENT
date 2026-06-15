from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.store import RunStore, Runner


def create_app(base_workdir: str | Path | None = None, runner: Runner | None = None) -> FastAPI:
    service_root = Path(__file__).resolve().parents[1]
    store = RunStore(base_workdir or service_root / "runs")
    app = FastAPI(title="Cashbook Reconciliation Service")
    app.state.store = store
    app.state.runner = runner or _default_runner

    frontend_dir = service_root / "frontend"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        index_path = frontend_dir / "index.html"
        if not index_path.exists():
            return "<h1>Cashbook Reconciliation Service</h1>"
        return index_path.read_text(encoding="utf-8")

    @app.post("/api/runs")
    def create_run(
        tolerance: Annotated[int, Form()] = 0,
        payment_file: Annotated[UploadFile, File()] = None,
        statement_files: Annotated[list[UploadFile], File()] = None,
        cashbook_file: Annotated[UploadFile, File()] = None,
    ) -> dict[str, str]:
        if payment_file is None or not statement_files or cashbook_file is None:
            raise HTTPException(status_code=400, detail="Payment, statement, and cashbook files are required")

        run_id = _reserve_run_id(store, tolerance)
        record = store.get(run_id)
        try:
            saved_payment = _save_upload(payment_file, record.workdir, "payment")
            saved_statements = [
                _save_upload(file, record.workdir, f"statement-{index + 1}")
                for index, file in enumerate(statement_files)
            ]
            saved_cashbook = _save_upload(cashbook_file, record.workdir, "cashbook")
            record.payment_file = saved_payment
            record.statement_files = saved_statements
            record.cashbook_file = saved_cashbook
            store.add_event(run_id, "status", {"status": "Files saved"})
        except Exception as exc:
            store.fail(run_id, str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _start_runner(app.state.runner, run_id, store)
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> StreamingResponse:
        _ensure_run(store, run_id)
        return StreamingResponse(store.iter_sse(run_id), media_type="text/event-stream")

    @app.get("/api/runs/{run_id}/alerts")
    def run_alerts(run_id: str) -> dict:
        record = _ensure_run(store, run_id)
        if record.result is None:
            raise HTTPException(status_code=409, detail="Run is not complete")
        return {
            "has_alerts": bool(record.result.get("alerts")),
            "accounts_processed": record.result.get("accounts_processed", 0),
            "alerts": record.result.get("alerts", []),
            "notes": record.result.get("notes", []),
        }

    @app.get("/api/runs/{run_id}/result.xlsx")
    def result_xlsx(run_id: str) -> FileResponse:
        record = _completed_run(store, run_id)
        return FileResponse(
            _existing_result_path(record.result, "result_path", "out_xlsx"),
            filename="Bao_cao_so_quy_updated.xlsx",
        )

    @app.get("/api/runs/{run_id}/alerts.md")
    def alerts_md(run_id: str) -> FileResponse:
        record = _completed_run(store, run_id)
        path = _existing_result_path(record.result, "alerts_path", "out_alerts")
        return FileResponse(path, media_type="text/markdown; charset=utf-8", filename="canh_bao_doi_chieu.md")

    return app


def _reserve_run_id(store: RunStore, tolerance: int) -> str:
    placeholder = store.base_workdir / ".placeholder"
    placeholder.touch(exist_ok=True)
    record = store.create(
        tolerance=tolerance,
        payment_file=placeholder,
        statement_files=[],
        cashbook_file=placeholder,
    )
    return record.run_id


def _save_upload(upload: UploadFile, workdir: Path, prefix: str) -> Path:
    suffix = Path(upload.filename or "").suffix
    destination = workdir / f"{prefix}{suffix}"
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    return destination


def _start_runner(runner: Runner, run_id: str, store: RunStore) -> None:
    if runner is _default_runner:
        thread = threading.Thread(target=runner, args=(run_id, store), daemon=True)
        thread.start()
        return
    runner(run_id, store)


def _default_runner(run_id: str, store: RunStore) -> None:
    from backend.graph import run_graph_for_store

    try:
        result = run_graph_for_store(run_id, store)
        store.complete(run_id, result)
    except Exception as exc:  # pragma: no cover - covered by integration/manual paths
        store.fail(run_id, str(exc))


def _ensure_run(store: RunStore, run_id: str):
    try:
        return store.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


def _completed_run(store: RunStore, run_id: str):
    record = _ensure_run(store, run_id)
    if record.result is None:
        raise HTTPException(status_code=409, detail="Run is not complete")
    return record


def _existing_result_path(result: dict | None, *keys: str) -> Path:
    if result is None:
        raise HTTPException(status_code=409, detail="Run is not complete")
    for key in keys:
        if value := result.get(key):
            path = Path(value)
            if path.exists():
                return path
    raise HTTPException(status_code=404, detail="Output file not found")


app = create_app()
