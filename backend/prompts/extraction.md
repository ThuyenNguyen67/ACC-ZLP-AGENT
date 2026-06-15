# Schema trích xuất (giai đoạn 1)

Subagent `excel-extractor` đọc từng file Excel và xuất ra JSON theo đúng schema dưới đây.
Các quy tắc chuẩn hóa (số tài khoản giữ số 0 đầu, số tiền bỏ dấu phẩy/VND…) do
`reconcile.py` áp dụng lại khi nạp; nhưng nên trích đúng giá trị thô để tránh sai số.

## input_1.json — từ File 1 (Payment)

Mỗi phần tử là một lệnh chi (giao dịch payment) có `Amount` hợp lệ. **Nhận dòng theo
`Amount`, không dựa vào cột `No`** (có dòng "nối" với `No` rỗng vẫn là giao dịch hợp lệ).

```json
{
  "payments": [
    {
      "amount": 30000000,
      "description": "Nap tien VDS Topup Viettel ... - CONG TY CO PHAN ZION",
      "sending_bank_number": "34582558456",
      "receiving_bank_number": ""
    }
  ]
}
```

| Trường | Cột nguồn (file mẫu) | Ghi chú |
|---|---|---|
| `amount` | `Amount` | Số tiền. |
| `description` | `Description` | Giữ nguyên văn bản gốc. |
| `sending_bank_number` | `Sending Bank Number` | Tài khoản chuyển → cash-out. |
| `receiving_bank_number` | `Receiving Bank Number` | Tài khoản nhận → cash-in nội bộ. **Thường rỗng** → để chuỗi rỗng `""`. |

## input_2.json - grouped File 2 bank statements

Each statement file becomes one item in `statements`. Account number and balances come from the text header, not the transaction table. Keep both credit rows and debit rows. Credit is cash-in and becomes a positive `amount`; debit is cash-out and becomes a negative `amount`. Drop totals rows and footer notes.

```json
{
  "statements": [
    {
      "bank_account_number": "34582558456",
      "end_balance_statement": 852089562,
      "begin_balance_statement": 9887407,
      "transactions": [
        { "amount": 94870000, "description": "NHAN TU 110002652993 ..." },
        { "amount": -30000000, "description": "Nap tien VDS Topup Viettel ..." }
      ]
    }
  ]
}
```

| Field | Source | Notes |
|---|---|---|
| `bank_account_number` | Header account text | String; preserve leading zeroes. |
| `end_balance_statement` | Header ending balance text | Used for reconciliation. |
| `begin_balance_statement` | Header beginning balance text | Optional cross-check only. |
| `transactions[].amount` | Credit/Debit Amount | Credit is positive cash-in; debit is negative cash-out. |
| `transactions[].description` | Trans.Detail | Preserve original text. |
## File 3 — KHÔNG trích xuất ra JSON

`reconcile.py` đọc trực tiếp File 3 gốc (.xlsx) bằng openpyxl để giữ template. Script tự
dò dòng header bằng cách tìm dòng chứa "Bank Account number" và "End Balance", rồi ánh xạ
các cột `Begin Balance`, `Cash In`, `Cash Out`, `End Balance` theo tên. Subagent không cần
xử lý File 3.
