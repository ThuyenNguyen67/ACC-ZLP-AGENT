from __future__ import annotations

from pathlib import Path
from typing import Any

from backend import reconcile as reconcile_module


def run_reconcile(
    *,
    payments_json: str | Path,
    statements_json: str | Path,
    cashbook_file: str | Path,
    out_xlsx: str | Path,
    out_alerts: str | Path,
    tolerance: int = 0,
) -> dict[str, Any]:
    payments = reconcile_module.load_payments(payments_json)
    statement_end_balances, statement_transactions = reconcile_module.load_statements(statements_json)
    accounts_processed, alerts, notes = reconcile_module.reconcile(
        payments,
        statement_end_balances,
        statement_transactions,
        cashbook_file,
        out_xlsx,
        tolerance,
    )
    reconcile_module.write_alerts_md(alerts, notes, out_alerts)
    return {
        "accounts_processed": accounts_processed,
        "alerts": alerts,
        "notes": notes,
        "out_xlsx": str(out_xlsx),
        "out_alerts": str(out_alerts),
    }
