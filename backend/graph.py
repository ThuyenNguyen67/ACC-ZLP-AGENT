from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from backend.nodes.executor import extract_payment_with_spec, extract_statement_with_spec
from backend.nodes.extract import OpenAISpecClient, SpecClient
from backend.nodes.reconcile_node import run_reconcile
from backend.store import RunStore


class ReconState(TypedDict, total=False):
    run_id: str
    workdir: str
    payment_file: str
    statement_files: list[str]
    statement_file: str
    cashbook_file: str
    tolerance: int
    spec_client: Any
    payments: dict[str, Any]
    statement_results: Annotated[list[dict[str, Any]], operator.add]
    input_1_path: str
    input_2_path: str
    result_path: str
    alerts_path: str
    alerts: list[dict[str, Any]]
    notes: list[str]
    accounts_processed: int
    has_alerts: bool
    status: str
    error: NotRequired[str]


def run_reconciliation(
    *,
    run_id: str,
    workdir: str | Path,
    payment_file: str | Path,
    statement_files: list[str | Path],
    cashbook_file: str | Path,
    tolerance: int,
    spec_client: SpecClient | None = None,
) -> dict[str, Any]:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    client = spec_client or OpenAISpecClient()
    graph = build_graph()
    final_state = graph.invoke(
        {
            "run_id": run_id,
            "workdir": str(workdir),
            "payment_file": str(payment_file),
            "statement_files": [str(path) for path in statement_files],
            "cashbook_file": str(cashbook_file),
            "tolerance": tolerance,
            "spec_client": client,
            "statement_results": [],
        }
    )
    return {
        "has_alerts": final_state["has_alerts"],
        "accounts_processed": final_state["accounts_processed"],
        "alerts": final_state["alerts"],
        "notes": final_state["notes"],
        "result_path": final_state["result_path"],
        "alerts_path": final_state["alerts_path"],
    }


def run_graph_for_store(run_id: str, store: RunStore) -> dict[str, Any]:
    record = store.get(run_id)
    if record.payment_file is None or record.cashbook_file is None or not record.statement_files:
        raise ValueError("Run is missing required files")

    store.add_event(run_id, "status", {"status": "Classifying"})
    store.add_event(run_id, "status", {"status": "Extracting"})
    result = run_reconciliation(
        run_id=run_id,
        workdir=record.workdir,
        payment_file=record.payment_file,
        statement_files=record.statement_files,
        cashbook_file=record.cashbook_file,
        tolerance=record.tolerance,
    )
    store.add_event(run_id, "status", {"status": "Completed"})
    return result


def build_graph():
    graph = StateGraph(ReconState)
    graph.add_node("classify_files", classify_files)
    graph.add_node("extract_payment", extract_payment)
    graph.add_node("extract_statement", extract_statement)
    graph.add_node("merge_statements", merge_statements)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "classify_files")
    graph.add_edge("classify_files", "extract_payment")
    graph.add_conditional_edges("extract_payment", dispatch_statements, ["extract_statement"])
    graph.add_edge("extract_statement", "merge_statements")
    graph.add_edge("merge_statements", "reconcile")
    graph.add_edge("reconcile", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def classify_files(state: ReconState) -> dict[str, Any]:
    required = [
        Path(state["payment_file"]),
        Path(state["cashbook_file"]),
        *[Path(path) for path in state["statement_files"]],
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing input file(s): " + ", ".join(missing))
    return {"status": "Classifying"}


def extract_payment(state: ReconState) -> dict[str, Any]:
    path = Path(state["payment_file"])
    spec = state["spec_client"].infer_payment_spec(path)
    payments = extract_payment_with_spec(path, spec)
    output_path = Path(state["workdir"]) / "input_1.json"
    output_path.write_text(json.dumps(payments, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "payments": payments,
        "input_1_path": str(output_path),
        "status": "Payment extracted",
    }


def dispatch_statements(state: ReconState) -> list[Send]:
    return [
        Send(
            "extract_statement",
            {
                "statement_file": statement_file,
                "workdir": state["workdir"],
                "spec_client": state["spec_client"],
            },
        )
        for statement_file in state["statement_files"]
    ]


def extract_statement(state: ReconState) -> dict[str, Any]:
    path = Path(state["statement_file"])
    spec = state["spec_client"].infer_statement_spec(path)
    statement = extract_statement_with_spec(path, spec)
    return {
        "statement_results": [statement],
    }


def merge_statements(state: ReconState) -> dict[str, Any]:
    output_path = Path(state["workdir"]) / "input_2.json"
    output = {"statements": state.get("statement_results", [])}
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"input_2_path": str(output_path), "status": "Statements merged"}


def reconcile_node(state: ReconState) -> dict[str, Any]:
    workdir = Path(state["workdir"])
    out_xlsx = workdir / "Bao_cao_so_quy_updated.xlsx"
    out_alerts = workdir / "canh_bao_doi_chieu.md"
    summary = run_reconcile(
        payments_json=state["input_1_path"],
        statements_json=state["input_2_path"],
        cashbook_file=state["cashbook_file"],
        out_xlsx=out_xlsx,
        out_alerts=out_alerts,
        tolerance=state["tolerance"],
    )
    return {
        "accounts_processed": summary["accounts_processed"],
        "alerts": summary["alerts"],
        "notes": summary["notes"],
        "result_path": str(out_xlsx),
        "alerts_path": str(out_alerts),
        "status": "Reconciled",
    }


def finalize(state: ReconState) -> dict[str, Any]:
    return {
        "has_alerts": bool(state.get("alerts")),
        "status": "Completed",
    }
