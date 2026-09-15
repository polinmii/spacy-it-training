# Deploy Term Scanner (`scripts/deploy_term_scanner.py`)

## 1. Overview

[deploy_term_scanner.py](../../scripts/deploy_term_scanner.py) is the primary production CLI tool for scanning batches of student OJT documents (portfolios, reports, logs) to identify and tabulate IT-related skills, tools, and platforms.

It implements a hybrid multi-stage scanning architecture:
1. **Official Verification**: Matches known terms against the curated dictionary ([it_terms.csv](../../data/it_terms.csv)) using case-insensitive phrase matching.
2. **Candidate Discovery**: Automatically surfaces unlisted technical terms using two independent discovery signals:
   - Syntactic context patterns via [context_miner.py](../../scripts/context_miner.py).
   - Statistical named entities recognized by the trained spaCy model.
3. **Structured Reporting**: Exports detailed audit trails, matrix pivot tables, and a candidate review queue to CSV.

---

## 2. Requirements & Dependencies

### Python Environment
- Python 3.10+
- `spacy >= 3.8.0`
- `pandas >= 2.0.0`
- `python-docx >= 1.0.0`
- `pdf2image >= 1.17.0`
- `pytesseract >= 0.3.10`

### System Libraries (Required for PDF OCR)
Scanned documents are extracted via Tesseract OCR page-by-page. Ensure the underlying system utilities are installed:
- Debian/Ubuntu:
  ```bash
  sudo apt update
  sudo apt install -y tesseract-ocr poppler-utils
  ```

---

## 3. Command-Line Arguments

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--input` | `Path` (Required) | — | Path to a single document or directory containing `.txt`, `.docx`, or `.pdf` files. |
| `--terms-csv` | `Path` (Required) | — | Path to the canonical terms CSV dictionary containing `term` and `label` columns. |
| `--model` | `Path` | `None` | Path to the trained spaCy NER model directory (`models/it_term_ner`). Required for `ner` and `hybrid` modes. |
| `--mode` | `Choice` | `hybrid` | Execution mode: `ruler`, `ner`, or `hybrid` (see details below). |
| `--context-mining` | `Choice` | `on` | Toggle untrained syntactic mining (`on` or `off`). Uses `en_core_web_sm` to mine unlisted terms. |
| `--output` | `Path` | `results/` | Target directory where generated CSV reports will be saved. |

---

## 4. Operational Modes

### A. `hybrid` Mode (Default & Recommended)
- Official counts come from exact dictionary matching against [it_terms.csv](../../data/it_terms.csv).
- Any entities predicted by the NER model or mined by [context_miner.py](../../scripts/context_miner.py) that are **not** in the dictionary are diverted into [candidate_new_terms.csv](../../_test_results/candidate_new_terms.csv).
- **Benefit**: Ensures strict statistical integrity for known competencies while continually expanding the technology catalogue.

### B. `ruler` Mode (Dictionary-Only)
- Relies solely on exact dictionary matching via `spacy.matcher.PhraseMatcher`.
- Requires no machine learning model or training checkpoint.
- **Benefit**: Extremely fast, deterministic, and ideal for quick auditing when no trained model is available.

### C. `ner` Mode (Model-Driven)
- Relies on the trained statistical model ([models/it_term_ner](../../models/it_term_ner)) to detect entities and assign category labels.
- Unlisted entities recognized by the model are assigned a fallback label `CANDIDATE` and folded into the primary count.

---

## 5. Ingestion & Memory Management

OJT submissions frequently consist of high-resolution scanned PDF portfolios exceeding 100 MB. To prevent out-of-memory (OOM) crashes:
- `_ocr_scanned_pdf()` queries total page count using `pdfinfo_from_path()`.
- It processes pages **one by one** using `convert_from_path(first_page=i, last_page=i, dpi=200)`.
- Each converted PIL image is fed into `pytesseract.image_to_string()` and immediately deleted from RAM before converting the subsequent page.

---

## 6. Output File Specifications

All outputs are written into the directory specified by `--output`:

### 1. `term_matches_detail.csv`
Granular line-item matches per document:
| Column | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `document` | `str` | `Flores_KristanR_OJT.pdf` | Document filename. |
| `term` | `str` | `MySQL` | Canonical term name from CSV. |
| `label` | `str` | `DATABASE` | Category label from CSV. |
| `count` | `int` | `4` | Number of times term appeared in the document. |

### 2. `term_matches_summary.csv`
A document-by-term pivot matrix.
- **Rows**: Each processed document.
- **Columns**: Every distinct term matched across the entire run.
- **Values**: Total count (fill value: `0`).

### 3. `category_summary.csv`
A document-by-category pivot matrix.
- **Rows**: Each processed document.
- **Columns**: Broad IT classifications (`DATABASE`, `PROG_LANG`, `FRAMEWORK`, `TOOL`, `CLOUD`, `OS`, `CONCEPT`, `METHODOLOGY`).
- **Values**: Aggregate occurrences of all tools in that category.

### 4. `candidate_new_terms.csv`
Discovered terms that do not exist in the dictionary, sorted by multi-document frequency:
| Column | Type | Description |
| :--- | :--- | :--- |
| `term` | `str` | Surface name of the discovered candidate. |
| `source` | `str` | Discovery mechanism (`context_pattern` or `ner`). |
| `count` | `int` | Cumulative frequency across all scanned files. |
| `documents` | `int` | Number of distinct documents mentioning the candidate. |
| `trigger_verb`| `str` | Verb that introduced the term (e.g., `migrate`, `deploy`). |
| `example_sentence`| `str` | Real sentence context extracted from the document. |

---

## 7. Execution Examples

### Basic Deployment Scan (Hybrid Mode)
```bash
python scripts/deploy_term_scanner.py \
    --input uploads/test \
    --terms-csv data/it_terms.csv \
    --model models/it_term_ner \
    --mode hybrid \
    --output results/
```

### Fast Dictionary-Only Scan (No Model Required)
```bash
python scripts/deploy_term_scanner.py \
    --input uploads/test \
    --terms-csv data/it_terms.csv \
    --mode ruler \
    --context-mining off \
    --output results/
```

### Scanning a Single Document
```bash
python scripts/deploy_term_scanner.py \
    --input "uploads/test/ODON_PORTFOLIO.pdf" \
    --terms-csv data/it_terms.csv \
    --model models/it_term_ner \
    --mode hybrid \
    --output results/single_doc
```
