# Financial Extractor Core

Financial Extractor Core is a high-performance backend service built on top of **FastAPI** with **Rust extensions (compiled via Maturin)** for lightning-fast parsing, text processing, and analysis. It allows users to extract transaction logs (from important Nigerian banks, which include OPay, GTBank, PalmPay, Moniepoint, UBA, Access Bank, and more.), receipts, and utility bills from PDFs, text, and images, as well as perform various verification tasks like address matching, reverse geocoding, and transaction auditing.

---

## 🚀 Key Features

- **High-Performance Document Extraction**: Extracts and sanitizes transaction lists, utility bills, and receipt line-items from PDFs, text files, and images.
- **OCR Support**: Leverages Tesseract OCR for extracting content from scanned receipts and images.
- **Fast Rust Core**: Extensible architecture incorporating a Rust extension module for optimized utility functions.
- **Address & Geo-Verification**:
  - Validates and normalizes US and international addresses.
  - Cross-references extracted receipt text with input addresses to prevent fraud or verify identity.
  - Reverse-geocodes latitude/longitude coordinates via Google Maps APIs.
- **Transaction Audit & Operations**:
  - Detects duplicate transactions based on description, amount, and date.
  - Filters transactions by ISO-8601 date ranges and amount/type thresholds.
  - Generates categorized transaction summaries (debits, credits, and counts per category).
  - Performs complex audit validations (detects missing data, future-dated transactions, excessive amounts).
- **Secure by Default**: Built-in API key authentication middleware.

---

## 📁 Folder Layout

```text
financial-extractor-core/
├── Cargo.toml            # Rust package manifest
├── Cargo.lock            # Rust dependency lockfile
├── pyproject.toml        # Maturin and Python project configuration
├── requirements.txt      # Python dependencies
├── ecosystem.config.js   # PM2 configuration for process management
├── main.py               # Application entrypoint & FastAPI setup
├── src/                  # Rust extension library source
├── middleware/           # FastAPI Middleware
├── routes/               # API Router Endpoints
├── services/             # Core Business Logic
├── utils/                # Helper utilities
├── tests/                # Automated pytest suite
└── data/                 # Raw/Input document directory (for offline tests/processing)
```

---

## 🛠️ Prerequisites

Ensure you have the following installed:

1. **Python**: 3.9 or higher.
2. **Rust & Cargo**: Required to compile the Rust extension. Install via [rustup](https://rustup.rs/).
3. **Tesseract OCR**: For scanned image/PDF extraction.
   - **Mac**: `brew install tesseract`
   - **Debian/Ubuntu**: `sudo apt-get install tesseract-ocr`
4. **Poppler**: Required by `pdf2image` for PDF-to-image conversion.
   - **Mac**: `brew install poppler`
   - **Debian/Ubuntu**: `sudo apt-get install poppler-utils`

---

## ⚙️ Setup & Installation

### 1. Clone & Navigate
```bash
git clone <repository-url>
cd financial-extractor-core
```

### 2. Configure Environment Variables
Create a `.env` file in the root directory:
```env
# Server Security
FINANCE_EXTRACTOR_CORE_API_KEY="your_secure_api_key_here"

# Google Maps API (Optional, required for reverse geocoding)
GOOGLE_MAPS_API_KEY="your_google_maps_api_key"
```

### 3. Build & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install maturin
maturin develop
```

---

## 🖥️ Running the Application

### Development
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### Production (PM2)
```bash
pm2 start ecosystem.config.js
```

---

## 📡 API Endpoints

### 🩺 Health & Diagnostic
| Endpoint | Method | Description |
|---|---|---|
| `/health` | `GET` | Returns app status, version, and uptime seconds. |

### 📄 Document Extraction
| Endpoint | Method | Description |
|---|---|---|
| `/receipts/extract` | `POST` | Extract structured data from a single receipt PDF or image. |

### 🔍 Verification & Audit
| Endpoint | Method | Description |
|---|---|---|
| `/verification/address` | `POST` | Normalizes and checks validity of US/International address objects. |
| `/verification/address-crossref` | `POST` | Validates an address and verifies its presence within raw receipt text. |
| `/verification/address-reverse` | `GET` | Resolves latitude/longitude coordinates into a validated address using Google. |
| `/verification/transactions/duplicates` | `POST` | Analyzes a transaction list to group duplicate records. |
| `/verification/transactions/filter-by-date` | `POST` | Filters transactions within a specified date range. |
| `/verification/transactions/filter-by-amount`| `POST` | Filters transactions using minimum/maximum amount thresholds. |
| `/verification/transactions/category-summary`| `POST` | Compiles an aggregated summary of transaction categories. |
| `/verification/transactions/validate` | `POST` | Audits transactions to report validation anomalies (future date, large sums, missing data). |

---

## 🧪 Testing

Run the full test suite:
```bash
pytest
```

Run only the per-bank API integration tests (requires sample PDFs in `data/`):
```bash
pytest tests/test_extraction_summary.py -v
```

The integration tests in `tests/test_extraction_summary.py` cover every supported bank and assert exact values for `detected_bank`, `confidence`, and all `summary` fields. Update this file when new banks are added or statement formats change — do not create new one-off scripts.

---

## ➕ Adding a New Bank

1. Create `services/banks/ng/<bank_code>.py` extending `BaseBankExtractor`.
2. Implement `detect(text)`, `extract_summary(text)`, and `extract(text, ...)`.
3. Register the extractor in `services/banks/ng/__init__.py` (`_EXTRACTORS` list).
4. Add a corresponding test block in `tests/test_extraction_summary.py`.
