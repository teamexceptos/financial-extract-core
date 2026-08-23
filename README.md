# Financial Extractor Core

Financial Extractor Core is a high-performance backend service built on top of **FastAPI** with **Rust extensions (compiled via Maturin)** for lightning-fast parsing, text processing, and analysis. It allows users to extract transaction logs, receipts, and utility bills from PDFs, text, and images, as well as perform various verification tasks like address matching, reverse geocoding, and transaction auditing.

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

Ensure you have the following installed on your system:

1. **Python**: Version 3.9 or higher.
2. **Rust & Cargo**: Required to compile the performance extensions. Install via [rustup](https://rustup.rs/).
3. **Tesseract OCR**: Required for OCR capabilities on scanned image documents.
   - **Mac (Homebrew)**: `brew install tesseract`
   - **Debian/Ubuntu**: `sudo apt-get install tesseract-ocr`
4. **Poppler**: Required by `pdf2image` to convert PDFs to images for OCR when necessary.
   - **Mac (Homebrew)**: `brew install poppler`
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
It's highly recommended to use a virtual environment:
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt

# Install Maturin for compiling Rust modules
pip install maturin

# Build and install the Rust extension in development mode
maturin develop
```

---

## 🖥️ Running the Application

### Running Locally (Development)
Start the FastAPI server with auto-reload:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Visit the interactive Swagger docs at: [http://localhost:8000/docs](http://localhost:8000/docs)

### Running in Production (PM2)
Manage the process in background mode using the configured PM2 profile:
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
| `/transactions/extract` | `POST` | Accepts a PDF/Image file and extracts all transactions. |
| `/transactions/bills` | `POST` | Accepts a PDF/Image and filters specifically for bill-like transactions. |

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

To run the automated test suite, execute:
```bash
pytest
```
