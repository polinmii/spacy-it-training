# Context Miner (`scripts/context_miner.py`)

## 1. Overview

[context_miner.py](../../scripts/context_miner.py) is a core NLP module responsible for **unsupervised syntactic candidate discovery**. It identifies technical tools, libraries, platforms, and frameworks directly from grammatical context within OJT documents—independent of whether they exist in the curated [it_terms.csv](../../data/it_terms.csv) or whether any model has been trained.

It operates by targeting common technical reporting patterns used by students, such as:
- *"I **integrated** the **Stripe API** into the payment gateway"*
- *"**Deployed** the application **using** **Kubernetes** and **Terraform**"*
- *"We **migrated** **to** **PostgreSQL**"*
- *"**Worked** **with** **Django** and **React**"*

### Dual Role in the System
1. **Training Time ([train_it_term_ner.ipynb](../../notebooks/train_it_term_ner.ipynb))**: Enriches auto-annotated training data with a generic `TECH_TERM` label. This teaches the statistical NER model to learn the syntactic *pattern* ("terms governed by these verbs and prepositions are technical tools") rather than merely memorizing vocabulary words.
2. **Deployment Time ([deploy_term_scanner.py](../../scripts/deploy_term_scanner.py))**: Surfaces uncataloged technologies from incoming student submissions without requiring model retraining, placing them into [candidate_new_terms.csv](../../_test_results/candidate_new_terms.csv) for human verification.

---

## 2. Requirements & Dependencies

- **Python Runtime**: Python 3.10+
- **Packages**: `spacy >= 3.8.0`
- **Trained Pipeline**: `en_core_web_sm` (provides Part-of-Speech tagging and syntactic dependency parsing)

Install via CLI:
```bash
python -m spacy download en_core_web_sm
```

---

## 3. Core Architecture & Matching Logic

### A. Syntactic Patterns (`DependencyMatcher`)
The module builds two dependency parse patterns using spaCy's `DependencyMatcher`:

1. **Direct Object Pattern (`TRIGGER_DIRECT_OBJECT`)**:
   - Matches a verb from `TRIGGER_LEMMAS` with a direct relationship (`dobj`, `attr`, `oprd`, `conj`) to a noun or proper noun.
   - Example: *"built [Docker container]"*
2. **Prepositional Object Pattern (`TRIGGER_PREP_OBJECT`)**:
   - Matches a verb from `TRIGGER_LEMMAS` $\rightarrow$ preposition (`prep` in `RELEVANT_PREPS`) $\rightarrow$ object of preposition (`pobj` in `NOUN` or `PROPN`).
   - Example: *"worked with [Kubernetes]"*, *"deployed on [AWS]"*

### B. Trigger Lemmas & Allowed Prepositions
- **`TRIGGER_LEMMAS`**: Curated list of 31 action verbs typical in technical OJT write-ups:
  `integrate`, `use`, `utilize`, `utilise`, `implement`, `deploy`, `adopt`, `leverage`, `employ`, `build`, `develop`, `configure`, `setup`, `migrate`, `install`, `learn`, `train`, `test`, `apply`, `handle`, `write`, `create`, `maintain`, `manage`, `automate`, `connect`, `query`, `host`, `containerize`, `run`, `debug`, `work`.
- **`RELEVANT_PREPS`**: Prepositions that establish a tool/platform relationship:
  `with`, `using`, `on`, `in`, `via`, `through`, `to`, `into`, `onto`, `from`.
  *(Temporal or causal prepositions such as `for`, `during`, or `since` are explicitly excluded to minimize false positives).*

### C. Heuristic Filtering (`_looks_like_tech_term`)
Generic nouns frequently follow these verbs (e.g., *"integrated the system"*, *"used my skills"*). To isolate genuine technical terms from generic prose, candidate noun phrases are passed through strict heuristic filters:

| Filter Criterion | Implementation | Description |
| :--- | :--- | :--- |
| **Stopword Exclusion** | `text.lower() in GENERIC_STOPWORDS` | Rejects common nouns (e.g., `system`, `project`, `team`, `report`, `database`, `code`, `workflow`). |
| **Length Limit** | `len(text.split()) > 4` | Rejects complex multi-word phrases over 4 words. |
| **Digit Detection** | `re.search(r"\d", text)` | Accepts versioned or alphanumeric terms (e.g., `Python 3`, `Vue3`, `Win11`). |
| **CamelCase Detection** | `re.match(r"^[a-z]+[A-Z]", text)` | Accepts software identifiers (e.g., `postgreSQL`, `jQuery`, `typeScript`). |
| **Acronym Detection** | `token.isupper() and len >= 2` | Accepts uppercase acronyms (e.g., `AWS`, `GCP`, `SQL`, `REST`, `API`). |
| **Title Case Detection** | `token[:1].isupper()` | Accepts capitalized proper names (e.g., `Django`, `Docker`, `Terraform`). |

### D. Span Cleaning & Determiner Trimming (`_trim_leading_det`)
When candidate tokens are identified within noun chunks, leading determiners (`DET`) and pronouns (`PRON`) like *"a"*, *"an"*, *"the"*, *"my"* are stripped.
- *"my Jira board"* $\rightarrow$ *"Jira board"*
- *"a GraphQL endpoint"* $\rightarrow$ *"GraphQL endpoint"*

---

## 4. API Reference

### Data Classes

#### `Candidate`
Represents an uncataloged entity discovered in context.
- `text: str` — Extracted surface text of the candidate term.
- `trigger_verb: str` — Lemma of the governing verb that triggered the match.
- `sentence: str` — Surrounding sentence providing context for human reviewers.

### Functions

#### `load_context_pipeline(model_name="en_core_web_sm") -> spacy.language.Language`
Loads the dependency parsing pipeline. Raises a descriptive `OSError` if the model wheel is missing.

#### `build_dep_matcher(nlp: spacy.language.Language) -> DependencyMatcher`
Constructs and registers the direct and prepositional dependency patterns onto the provided pipeline's vocabulary.

#### `mine_candidates(text: str, nlp: Language, matcher: DependencyMatcher, known_terms_lower: set | None = None) -> list[Candidate]`
Parses the input text, executes syntactic matching, applies heuristic filtering, and deduplicates candidates.
- Returns a list of unique `Candidate` instances per document (storing the first occurrence's sentence).
- Ignores terms found in `known_terms_lower` (the curated CSV dictionary).

---

## 5. Usage Example

```python
import spacy
from scripts.context_miner import (
    load_context_pipeline,
    build_dep_matcher,
    mine_candidates
)

# 1. Load pipeline and matcher
nlp = load_context_pipeline("en_core_web_sm")
matcher = build_dep_matcher(nlp)

# 2. Define known vocabulary to exclude
known_csv_terms = {"python", "mysql", "git"}

# 3. Sample document sentence
text = "During my internship, I deployed microservices using Kubernetes and configured Prometheus for monitoring."

# 4. Mine new candidates
candidates = mine_candidates(text, nlp, matcher, known_terms_lower=known_csv_terms)

for cand in candidates:
    print(f"Candidate: {cand.text}")
    print(f"Trigger:   {cand.trigger_verb}")
    print(f"Sentence:  {cand.sentence}\n")
```

**Output:**
```
Candidate: Kubernetes
Trigger:   deploy
Sentence:  During my internship, I deployed microservices using Kubernetes and configured Prometheus for monitoring.

Candidate: Prometheus
Trigger:   configure
Sentence:  During my internship, I deployed microservices using Kubernetes and configured Prometheus for monitoring.
```
