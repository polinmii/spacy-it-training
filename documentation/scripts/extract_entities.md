# Extract Entities Backend (`scripts/extract_entities.py`)

## 1. Overview

[extract_entities.py](../../scripts/extract_entities.py) is a specialized production backend service designed to bridge the Python NLP pipeline with external web portals (specifically PHP frontends) and a MySQL database.

When given the path to an uploaded student PDF, the script:
1. Extracts text from the PDF using `pdfplumber`.
2. Connects to the local MySQL database (`nbsc_ojt`) and fetches the `predefined_entities` table (including aliases, categories, activity classifications, and descriptions).
3. Matches and normalizes terms, resolving overlapping spans (e.g., ensuring "Windows 11" takes precedence over "Windows").
4. Classifies mentions into **Software**, **Hardware**, **Clerical**, and **Other**, calculating IT vs. Clerical percentages.
5. Emits a clean JSON response directly to standard output (`stdout`) for the PHP frontend to ingest and render.

---

## 2. Requirements & Dependencies

### Python Dependencies
- `pdfplumber >= 0.11.0`
- `spacy >= 3.8.0`
- `mysql-connector-python >= 8.0.0`
- Model: `en_core_web_sm`

### Database Dependency
Requires an active MySQL instance hosting the OJT database:
- **Host**: `127.0.0.1`
- **Port**: `3306`
- **Database**: `nbsc_ojt`
- **User**: `root`
- **Password**: `""` (configurable in script header)

### Expected MySQL Schema
The script expects a table named `predefined_entities` with the following columns:
```sql
CREATE TABLE predefined_entities (
    id INT AUTO_INCREMENT PRIMARY KEY,
    entity_name VARCHAR(255) NOT NULL,
    aliases TEXT,
    category VARCHAR(100),
    activity_type ENUM('Software', 'Hardware', 'Clerical', 'Other') DEFAULT 'Other',
    it_related ENUM('yes', 'no', 'unknown') DEFAULT 'unknown',
    description TEXT
);
```

---

## 3. Execution & Interface

The script is invoked via command line, passing the absolute or relative path to the PDF document as an argument:

```bash
python scripts/extract_entities.py "uploads/test/ODON_PORTFOLIO.pdf"
```

### Invocation from PHP
In a PHP web application, the script is typically called via `shell_exec` or `proc_open`:

```php
<?php
$pdfPath = escapeshellarg('/var/www/uploads/student_report.pdf');
$command = "python3 " . escapeshellarg('/path/to/spacy/scripts/extract_entities.py') . " $pdfPath";
$output = shell_exec($command);
$data = json_decode($output, true);

if ($data && $data['success']) {
    $itPercent = $data['summary']['it_percentage'];
    $clericalPercent = $data['summary']['clerical_percentage'];
    $entities = $data['entities'];
    // Render dashboard or update student evaluation record...
} else {
    $errorMsg = $data['error'] ?? 'Unknown extraction error';
    // Handle error...
}
?>
```

---

## 4. Text Processing & Overlap Resolution

### Text Normalization (`normalize`)
Text and dictionary terms undergo Unicode NFKC normalization, lowercase conversion, hyphen-to-space substitution, and punctuation removal (preserving `+`, `#`, `.`, and letters/digits):
- `"C# Programming"` $\rightarrow$ `"c# programming"`
- `"Node.js"` $\rightarrow$ `"node.js"`
- `"PHP-Developer"` $\rightarrow$ `"php developer"`

### Alias Handling (`split_aliases`)
Entities can have multiple aliases stored as pipe, comma, or semicolon-delimited strings:
`"PHP | PHP Language | PHP Programming"` $\rightarrow$ `["PHP", "PHP Language", "PHP Programming"]`.

### Overlap Disambiguation (`resolve_entity_overlaps`)
To avoid double-counting sub-phrases (e.g. counting both "Windows" and "Windows 11" when the text states "installed Windows 11"):
1. The script identifies character offsets of all occurrences across all candidate entity terms.
2. Candidates are sorted by length descending.
3. Longer spans claim the character range, preventing shorter nested substrings from triggering redundant matches.
4. Independent occurrences appearing elsewhere in the document are preserved.

---

## 5. Summary Metrics & Categorization

The script aggregates frequencies and calculates relative technical composition:

| Metric | Calculation |
| :--- | :--- |
| `it_percentage` | `(it_related_count / (it_related_count + clerical_count)) * 100` |
| `clerical_percentage` | `(clerical_count / (it_related_count + clerical_count)) * 100` |
| `software_percentage` | `(software_count / total_frequency) * 100` |
| `hardware_percentage` | `(hardware_count / total_frequency) * 100` |
| `clerical_activity_percentage` | `(clerical_count / total_frequency) * 100` |
| `other_percentage` | `(other_count / total_frequency) * 100` |

---

## 6. Output JSON Schema

### Success Response
```json
{
  "success": true,
  "content": "--- PAGE 1 ---\nDaily Accomplishment Report...",
  "entity_count": 4,
  "entities": [
    {
      "predefined_id": 16,
      "entity": "MySQL",
      "entity_name": "MySQL",
      "canonical_name": "MySQL",
      "category": "Database",
      "activity_type": "Software",
      "it_related": "yes",
      "description": "Relational database management system",
      "matched_term": "MySQL",
      "frequency": 5,
      "source": "predefined"
    }
  ],
  "summary": {
    "total": 12,
    "unique_entities": 4,
    "it_related": 10,
    "clerical": 2,
    "it_percentage": 83.33,
    "clerical_percentage": 16.67,
    "software": 8,
    "hardware": 2,
    "clerical_activity": 2,
    "other": 0,
    "software_percentage": 66.67,
    "hardware_percentage": 16.67,
    "clerical_activity_percentage": 16.67,
    "other_percentage": 0.0
  },
  "spacy_entities": [
    {"text": "John Doe", "label": "PERSON"}
  ]
}
```

### Error Response
If a failure occurs (file missing, bad PDF structure, MySQL offline), the script returns exit code `1` and a JSON error payload:
```json
{
  "success": false,
  "error": "Unable to connect to MySQL.",
  "details": "2003 (HY000): Can't connect to MySQL server on '127.0.0.1:3306'"
}
```
