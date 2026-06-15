from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from backend.nodes.executor import (
    PaymentExtractionSpec,
    StatementExtractionSpec,
    extract_payment_with_spec,
    extract_statement_with_spec,
)


DEFAULT_LLM_BASE_URL = "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
DEFAULT_LLM_MODEL = "minimax/minimax-m2.5"

# Transient API errors (network blip, 5xx, rate limit) are retried inside the
# HTTP client with backoff; validation failures are retried at a higher level
# by feeding the rejection reason back to the model.
MAX_API_RETRIES = 3
MAX_VALIDATION_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 60


class SpecInferenceError(RuntimeError):
    """The LLM could not produce a usable extraction spec after all retries."""


class SpecClient(Protocol):
    def infer_payment_spec(self, path: Path) -> PaymentExtractionSpec:
        ...

    def infer_statement_spec(self, path: Path) -> StatementExtractionSpec:
        ...


class OpenAISpecClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        max_validation_attempts: int = MAX_VALIDATION_ATTEMPTS,
    ):
        resolved_api_key = api_key or os.getenv("LLM_API_KEY")
        if not resolved_api_key:
            raise ValueError("LLM_API_KEY is required for GreenNode AI Platform access")

        self.model = model or os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
        self.max_validation_attempts = max_validation_attempts
        self.llm = ChatOpenAI(
            model=self.model,
            base_url=base_url or os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            api_key=resolved_api_key,
            temperature=0,
            max_retries=MAX_API_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def infer_payment_spec(self, path: Path) -> PaymentExtractionSpec:
        return self._infer_with_retry(
            label="payment",
            base_prompt=_payment_prompt(path),
            spec_model=PaymentExtractionSpec,
            dry_run=lambda spec: _dry_run_payment(path, spec),
        )

    def infer_statement_spec(self, path: Path) -> StatementExtractionSpec:
        return self._infer_with_retry(
            label="statement",
            base_prompt=_statement_prompt(path),
            spec_model=StatementExtractionSpec,
            dry_run=lambda spec: _dry_run_statement(path, spec),
        )

    def _infer_with_retry(self, *, label, base_prompt, spec_model, dry_run):
        """Ask the LLM for a spec, validate it against the real file, and on
        failure feed the rejection reason back so the next attempt can self-correct.

        temperature=0 alone would just repeat the same wrong answer; appending the
        concrete error to the prompt is what makes a retry meaningfully different.
        """
        feedback = ""
        last_error: Exception | None = None
        for _ in range(self.max_validation_attempts):
            content = self._invoke(base_prompt + feedback)
            try:
                spec = spec_model.model_validate(parse_llm_json(content))
                dry_run(spec)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                feedback = _correction_feedback(exc, content)
                continue
            return spec
        raise SpecInferenceError(
            f"Could not infer a valid {label} extraction spec after "
            f"{self.max_validation_attempts} attempts. Last error: {last_error}"
        )

    def _invoke(self, prompt: str) -> str:
        response = self.llm.invoke(prompt)
        return str(response.content)


def _dry_run_payment(path: Path, spec: PaymentExtractionSpec) -> None:
    """Surface a wrong spec as an error before the pipeline trusts it.

    extract_payment_with_spec already raises ValueError for unresolvable columns;
    an empty result almost always means header_row/data_start_row are off."""
    result = extract_payment_with_spec(path, spec)
    if not result["payments"]:
        raise ValueError(
            "Spec produced 0 payment rows; header_row, data_start_row, or the "
            "amount column is likely wrong."
        )


def _dry_run_statement(path: Path, spec: StatementExtractionSpec) -> None:
    result = extract_statement_with_spec(path, spec)
    if not result["transactions"]:
        raise ValueError(
            "Spec produced 0 transactions; header_row, data_start_row, or the "
            "credit/debit columns are likely wrong."
        )


def _correction_feedback(error: Exception, previous_answer: str) -> str:
    return (
        "\n\n---\n"
        "Your previous JSON was REJECTED. Read the Excel sample again and fix it.\n"
        f"Error: {error}\n"
        f"Previous answer:\n{previous_answer}\n"
        "Return ONLY corrected JSON, no explanation."
    )


def parse_llm_json(content: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL)
    payload = fenced.group(1) if fenced else content
    payload = payload.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(payload[start : end + 1])


def _payment_prompt(path: Path) -> str:
    return f"""You infer a JSON extraction spec for ONE Payment Excel file.
Return only valid JSON matching:
{{
  "header_row": 1,
  "data_start_row": 2,
  "columns": {{
    "amount": "Amount",
    "description": "Description",
    "sending_bank_number": "Sending Bank Number",
    "receiving_bank_number": "Receiving Bank Number"
  }},
  "row_filter": "amount_not_empty"
}}

Rules:
- Use 1-based row numbers.
- Columns may be exact header names or 1-based numeric indexes.
- Keep rows by valid Amount, not by No.

Excel sample:
{_sample_sheet(path)}
"""


def _statement_prompt(path: Path) -> str:
    return f"""You infer a JSON extraction spec for ONE Vietnamese bank statement Excel file.
Return only valid JSON matching:
{{
  "header_scan_rows": 40,
  "header_row": 16,
  "data_start_row": 17,
  "account_regex": "Số tài khoản:\\\\s*(\\\\d+)",
  "begin_balance_regex": "Số dư đầu kỳ:\\\\s*([\\\\d,.\\\\s]+)",
  "end_balance_regex": "Số dư cuối kỳ:\\\\s*([\\\\d,.\\\\s]+)",
  "columns": {{
    "credit": "Số tiền ghi có",
    "debit": "Số tiền ghi nợ",
    "description": "Nội dung giao dịch"
  }}
}}

Rules:
- Use 1-based row numbers.
- Account number and balances are in the text header.
- Always include both the "credit" and "debit" columns.
- Keep credit rows as money in and debit rows as money out; the executor will
  turn them into signed transactions.
- Drop totals and footnotes (a totals row fills both credit and debit, has no
  STT, and may leak a balance number into the description column).

Excel sample:
{_sample_sheet(path)}
"""


def _sample_sheet(path: Path) -> str:
    df = pd.read_excel(path, header=None, nrows=40)
    return df.to_string(max_rows=40, max_cols=30)
