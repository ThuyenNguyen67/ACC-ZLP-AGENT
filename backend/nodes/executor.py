from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from backend.reconcile import norm_acct, to_amount


ColumnRef = str | int


class PaymentExtractionSpec(BaseModel):
    header_row: int = Field(ge=1)
    columns: dict[str, ColumnRef]
    row_filter: Literal["amount_not_empty"] = "amount_not_empty"
    data_start_row: int | None = Field(default=None, ge=1)
    sheet_name: str | int | None = None


class StatementExtractionSpec(BaseModel):
    header_scan_rows: int = Field(default=40, ge=1)
    header_row: int = Field(ge=1)
    data_start_row: int | None = Field(default=None, ge=1)
    account_regex: str
    begin_balance_regex: str | None = None
    end_balance_regex: str
    columns: dict[str, ColumnRef]
    sheet_name: str | int | None = None

    @model_validator(mode="after")
    def require_transaction_columns(self) -> "StatementExtractionSpec":
        missing = {"credit", "debit", "description"} - set(self.columns)
        if missing:
            raise ValueError(
                "Statement spec requires columns: "
                + ", ".join(sorted(missing))
            )
        return self


def extract_payment_with_spec(path: str | Path, spec: PaymentExtractionSpec) -> dict[str, Any]:
    df = _read_sheet(path, spec.sheet_name)
    amount_col = _resolve_column(df, spec.header_row, spec.columns["amount"])
    description_col = _resolve_column(df, spec.header_row, spec.columns["description"])
    sending_col = _resolve_column(df, spec.header_row, spec.columns["sending_bank_number"])
    receiving_col = _resolve_column(df, spec.header_row, spec.columns["receiving_bank_number"])
    start_row = (spec.data_start_row or spec.header_row + 1) - 1

    payments = []
    for _, row in df.iloc[start_row:].iterrows():
        amount = to_amount(row.iloc[amount_col])
        if spec.row_filter == "amount_not_empty" and amount <= 0:
            continue
        payments.append(
            {
                "amount": amount,
                "description": _cell_text(row.iloc[description_col]),
                "sending_bank_number": norm_acct(_cell_text(row.iloc[sending_col])),
                "receiving_bank_number": norm_acct(_cell_text(row.iloc[receiving_col])),
            }
        )
    return {"payments": payments}


def extract_statement_with_spec(path: str | Path, spec: StatementExtractionSpec) -> dict[str, Any]:
    df = _read_sheet(path, spec.sheet_name)
    header_text = _header_text(df, max(spec.header_scan_rows, spec.header_row))
    credit_col = _resolve_column(df, spec.header_row, spec.columns["credit"])
    debit_col = _resolve_column(df, spec.header_row, spec.columns["debit"])
    description_col = _resolve_column(df, spec.header_row, spec.columns["description"])
    start_row = (spec.data_start_row or spec.header_row + 1) - 1

    statement = {
        "bank_account_number": norm_acct(_regex_group(spec.account_regex, header_text, "account")),
        "end_balance_statement": to_amount(
            _regex_group(spec.end_balance_regex, header_text, "end balance")
        ),
        "transactions": [],
    }
    if spec.begin_balance_regex:
        statement["begin_balance_statement"] = to_amount(
            _regex_group(spec.begin_balance_regex, header_text, "begin balance")
        )

    for _, row in df.iloc[start_row:].iterrows():
        credit = to_amount(row.iloc[credit_col])
        debit = to_amount(row.iloc[debit_col])
        if credit > 0 and debit > 0:
            # A totals/summary row aggregates both columns at once; a real
            # transaction is either a credit or a debit, never both.
            continue
        if credit <= 0 and debit <= 0:
            continue
        description = _cell_text(row.iloc[description_col])
        if not description:
            continue
        if _is_totals_description(description):
            # Closing totals/footer rows have no narrative description; some bank
            # exports leak a balance number into this column. A real transaction
            # detail always contains text, so a number-only cell marks a non-row.
            continue
        amount = credit if credit > 0 else -debit
        statement["transactions"].append({"amount": amount, "description": description})
    return statement


def _is_totals_description(description: str) -> bool:
    return not any(ch.isalpha() for ch in description)


def _read_sheet(path: str | Path, sheet_name: str | int | None) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=0 if sheet_name is None else sheet_name, header=None)


def _resolve_column(df: pd.DataFrame, header_row: int, ref: ColumnRef) -> int:
    if isinstance(ref, int):
        index = ref - 1
        if index < 0 or index >= len(df.columns):
            raise ValueError(f"Column index {ref} is outside the sheet")
        return index

    header = df.iloc[header_row - 1]
    expected = _normal_label(ref)
    for index, value in enumerate(header):
        if _normal_label(_cell_text(value)) == expected:
            return index
    raise ValueError(f"Column '{ref}' not found on header row {header_row}")


def _header_text(df: pd.DataFrame, rows: int) -> str:
    pieces = []
    for _, row in df.iloc[:rows].iterrows():
        cells = [_cell_text(value) for value in row]
        pieces.append(" ".join(cell for cell in cells if cell))
    return "\n".join(pieces)


def _regex_group(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find {label} with regex {pattern!r}")
    return match.group(1)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _normal_label(value: str) -> str:
    return " ".join(value.lower().split())
