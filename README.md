# ACC-ZLP-AGENT — Cashbook Reconciliation Agent

> Agent đối chiếu sổ quỹ cuối ngày tự động dành cho kế toán ZaloPay

---

## Vấn đề giải quyết

Cuối mỗi ngày, kế toán phải đối chiếu thủ công nhiều nguồn dữ liệu:

- **File thanh toán** (payment file)
- **Sao kê ngân hàng** (bank statements — nhiều file, mỗi ngân hàng một định dạng Excel khác nhau)
- **Sổ quỹ** (cashbook)

Mục tiêu: tổng hợp số dư cuối kỳ và phát hiện sai lệch trong các giao dịch phát sinh trong ngày.

Việc này tốn nhiều thời gian, dễ sai sót, và lượng dòng dữ liệu có thể lên đến **hàng triệu bản ghi**.

---

## Người dùng mục tiêu

Kế toán cần:
1. Tải file Excel lên (payment, bank statements, cashbook)
2. Bấm một nút
3. Nhận kết quả đối chiếu và cảnh báo sai lệch — **không cần thao tác thủ công**

---

## Cách agent hoạt động

```
┌─────────────────────────────────────────────────────────────┐
│                        Upload Files                          │
│   payment.xlsx   +   statement_*.xlsx   +   cashbook.xlsx   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  AI Extract  │  ← LangGraph + LLM
                    │  (per file)  │    đọc hiểu cấu trúc
                    └──────┬───────┘    trích xuất: account_number,
                           │            amount, description, date
                    ┌──────▼──────┐
                    │  Reconcile   │  ← Python / Pandas
                    │    Node      │    tính cash_in, cash_out
                    └──────┬───────┘    số dư cuối kỳ, cộng dồn
                           │            giao dịch theo ngày
              ┌────────────┴────────────┐
              │                         │
       ┌──────▼──────┐          ┌───────▼──────┐
       │  result.xlsx │          │  alerts.md   │
       │  Sổ quỹ      │          │  Cảnh báo    │
       │  cập nhật    │          │  sai lệch    │
       └─────────────┘          └──────────────┘
```

### Chi tiết xử lý

| Bước | Mô tả |
|------|-------|
| **Extract** | AI đọc hiểu format của từng file Excel (payment, bank statement), trích xuất các trường chuẩn hóa: `bank_account_number`, `amount`, `description`, `transaction_date` |
| **Reconcile** | Xử lý logic: ghép giao dịch payment ↔ bank statement, tính `cash_in` / `cash_out`, cộng dồn theo ngày vào sổ quỹ, tính số dư cuối kỳ |
| **Alert** | Phát hiện mismatch, xuất file cảnh báo kèm danh sách giao dịch chênh lệch để kế toán truy vết |

---

## Giá trị mang lại

- **Tiết kiệm thời gian**: từ đối chiếu thủ công hàng giờ xuống còn **vài phút**, chỉ một thao tác upload
- **Chính xác**: số liệu được xử lý bởi AI + logic tính toán, phát hiện sai lệch tự động
- **Linh hoạt**: xử lý được **file lớn** (hàng triệu dòng) và **nhiều định dạng file** khác nhau của các ngân hàng
- **Dễ truy vết**: file cảnh báo kèm chi tiết giao dịch cash_in / cash_out giúp kế toán nhanh chóng xác định nguồn sai lệch

---

## Tech Stack

| Layer | Công nghệ |
|-------|-----------|
| Backend API | FastAPI + Uvicorn |
| AI / Agent | LangGraph + LangChain (OpenAI-compatible) |
| Data processing | Pandas, openpyxl, xlrd |
| Frontend | Vanilla HTML / CSS / JS |
| Containerization | Docker |

---

## Cài đặt & Chạy

### Yêu cầu

- Python 3.10+
- Docker (tùy chọn)
- API key tương thích OpenAI (VNG GreenNode AIP hoặc tương đương)

### Cấu hình môi trường

```bash
cp .env.example .env
# Chỉnh sửa .env, điền LLM_API_KEY của bạn
```

```env
LLM_BASE_URL=https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1
LLM_MODEL=minimax/minimax-m2.5
LLM_API_KEY=<your-api-key>
```

### Chạy với Python

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8000
```

Mở trình duyệt tại `http://localhost:8000`

### Chạy với Docker

```bash
docker build -t acc-zlp-agent .
docker run -p 8000:8000 --env-file .env acc-zlp-agent
```

---

## API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/` | Giao diện web |
| `GET` | `/health` | Health check |
| `POST` | `/api/runs` | Tạo run mới, upload files |
| `GET` | `/api/runs/{run_id}/events` | Stream tiến trình (SSE) |
| `GET` | `/api/runs/{run_id}/alerts` | Kết quả đối chiếu (JSON) |
| `GET` | `/api/runs/{run_id}/result.xlsx` | Tải sổ quỹ đã cập nhật |
| `GET` | `/api/runs/{run_id}/alerts.md` | Tải file cảnh báo sai lệch |

### Ví dụ upload

```bash
curl -X POST http://localhost:8000/api/runs \
  -F "payment_file=@payment.xlsx" \
  -F "statement_files=@bank_vietcombank.xlsx" \
  -F "statement_files=@bank_techcombank.xlsx" \
  -F "cashbook_file=@cashbook.xlsx" \
  -F "tolerance=0"
```

---

## Cấu trúc project

```
ACC-ZLP-AGENT/
├── backend/
│   ├── app.py              # FastAPI application
│   ├── graph.py            # LangGraph workflow
│   ├── reconcile.py        # Reconciliation logic
│   ├── store.py            # Run state management
│   ├── nodes/
│   │   ├── extract.py      # AI extraction node
│   │   ├── executor.py     # Execution node
│   │   └── reconcile_node.py
│   └── prompts/
│       └── extraction.md   # LLM prompt for file parsing
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Tác giả

Phát triển bởi team ZaloPay — ACC Hackathon submission.
