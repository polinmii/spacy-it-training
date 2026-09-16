# Supplemental Training Sentences

This directory holds hand-written training sentences in JSON format. These
supplement the OCR-derived training data from `_text_cache/` with clean, curated
examples — especially useful for IT tasks the OCR documents don't cover well.

## Format (Option C — both annotated and plain)

Each `.json` file is an array of sentence objects. Two formats are supported in
the same file:

### Annotated (gold labels)

Provide `entities` with character-offset spans `[start, end, "LABEL"]`:

```json
{
  "text": "Deployed the app using Docker and Kubernetes.",
  "entities": [[22, 28, "TOOL"], [33, 43, "TOOL"]]
}
```

> **Offsets are 0-indexed, exclusive-end** (Python slice style).
> `text[22:28]` → `"Docker"`, `text[33:43]` → `"Kubernetes"`.

### Task Classification (`cats` for TextCat)

Provide `cats` alongside `entities` to train the pipeline to classify sentences/tasks:

```json
{
  "text": "Encoding data in Excel",
  "entities": [[0, 22, "IT_TASK"]],
  "cats": {
    "IT_TASK": 1.0,
    "CLERICAL": 0.0
  }
}
```


### Plain text (auto-annotated)

Omit `entities` — the notebook's EntityRuler and context miner will
auto-annotate at training time:

```json
{
  "text": "Performed software updates and virus scanning on all office computers."
}
```

## Adding new sentences

1. Create a new `.json` file (or add entries to an existing one).
2. Re-run the notebook from **Step 4b** onward.

Any `.json` file in this directory will be picked up automatically.

## Files in this directory

- `seed_examples.json`: Hand-curated tasks from `IT_related_task_and_clerical_task.md` labelled as `IT_TASK` (280 examples) or `CLERICAL` (137 examples).
- `tool_examples.json`: Curated examples with explicit tool, framework, and language annotations (`TOOL`, `PROG_LANG`, `FRAMEWORK`).

## Labels

Supported entity labels include categories from `data/it_terms.csv` plus task-level classifications:

| Label        | Description                                    |
|--------------|------------------------------------------------|
| IT_TASK      | IT-related activity, development, or hardware  |
| CLERICAL     | Administrative, document handling, or clerical |
| PROG_LANG    | Programming language                           |
| FRAMEWORK    | Framework or library                           |
| DATABASE     | Database system                                |
| TOOL         | Development or office tool                     |
| CLOUD        | Cloud platform                                 |
| OS           | Operating system                               |
| CONCEPT      | IT concept or methodology                      |
| METHODOLOGY  | Development methodology                        |
| TECH_TERM    | Generic (let the model categorize)             |
