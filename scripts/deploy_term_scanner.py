#!/usr/bin/env python3
"""
deploy_term_scanner.py
-----------------------
Scan OJT documents for IT-related terms and store/count matches to CSV.

Modes (official counts — always from the predefined CSV, in every mode):
  ruler   -- exact matching against the predefined terms CSV only (fast, precise,
             no model needed beyond the CSV; use this if you haven't trained an NER
             model yet).
  ner     -- use a trained spaCy NER model to also count matches (in addition to
             exact CSV matches), for terms the model recognizes as a known category.
  hybrid  -- (default, recommended) same official counts as ruler, but also cross-
             checks with a trained NER model for extra candidates.

Term *discovery* (terms not in the CSV) runs independently of --mode, via two
signals, both written to candidate_new_terms.csv for you to review and promote
into the CSV:
  - "context_pattern": found by context_miner.py (no model needed) -- phrasing like
    "integrated <X>" / "worked with <X>". On by default; disable with
    --context-mining off.
  - "ner": entities the trained NER model recognized that aren't in the CSV
    (only in ner/hybrid mode, and only once a model has been trained).

Usage:
  python deploy_term_scanner.py \\
      --input path/to/ojt_documents \\
      --terms-csv it_terms.csv \\
      --model models/it_term_ner \\
      --mode hybrid \\
      --output results/

Outputs (written to --output):
  term_matches_detail.csv   -- one row per (document, term) with count
  term_matches_summary.csv  -- pivot: one row per document, one column per term, counts
  category_summary.csv      -- one row per document, one column per category, counts
  candidate_new_terms.csv   -- terms found but NOT in the predefined CSV, with source
                               (context_pattern / ner), example sentence, and counts
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import spacy
from spacy.matcher import PhraseMatcher

from context_miner import build_dep_matcher, load_context_pipeline, mine_candidates

SUPPORTED_EXTENSIONS = {".txt", ".docx", ".pdf"}


def _ocr_scanned_pdf(pdf_path: Path) -> str:
    """OCR fallback for scanned PDFs — processes one page at a time to keep RAM low."""
    from pdf2image import convert_from_path, pdfinfo_from_path
    import pytesseract

    info = pdfinfo_from_path(str(pdf_path))
    total_pages = info["Pages"]
    print(f"  [OCR] {pdf_path.name}: {total_pages} page(s) at 200 DPI (one at a time)...")

    extracted_text = []
    for page_num in range(1, total_pages + 1):
        pages = convert_from_path(
            str(pdf_path), dpi=200,
            first_page=page_num, last_page=page_num,
        )
        text = pytesseract.image_to_string(pages[0])
        extracted_text.append(text)
        del pages, text

    result = "\n".join(extracted_text)
    del extracted_text
    return result


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".docx":
        import docx
        d = docx.Document(str(path))
        return "\n".join(p.text for p in d.paragraphs)
    if suffix == ".pdf":
        text = _ocr_scanned_pdf(path)
        return text
    raise ValueError(f"Unsupported file type: {suffix}")


def load_terms(terms_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(terms_csv)
    df["term"] = df["term"].astype(str).str.strip()
    df["label"] = df["label"].astype(str).str.strip().str.upper()
    if not {"term", "label"}.issubset(df.columns):
        raise ValueError("terms CSV must have 'term' and 'label' columns")
    return df


def build_phrase_matcher(nlp: spacy.language.Language, terms_df: pd.DataFrame) -> PhraseMatcher:
    matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    by_label = defaultdict(list)
    for row in terms_df.itertuples():
        by_label[row.label].append(row.term)
    for label, terms in by_label.items():
        patterns = [nlp.make_doc(t) for t in terms]
        matcher.add(label, patterns)
    return matcher


def collect_documents(input_path: Path):
    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {input_path.suffix}")
        return [input_path]
    docs = sorted(
        p for p in input_path.glob("**/*") if p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not docs:
        raise ValueError(f"No supported documents ({SUPPORTED_EXTENSIONS}) found in {input_path}")
    return docs


def scan_with_ruler(text: str, blank_nlp, matcher: PhraseMatcher) -> Counter:
    """Exact matches against the predefined term list. Returns Counter[term_text -> count]."""
    doc = blank_nlp.make_doc(text)
    matches = matcher(doc)
    counts = Counter()
    for match_id, start, end in matches:
        span_text = doc[start:end].text
        counts[span_text] += 1
    return counts


def scan_with_ner(text: str, ner_nlp) -> Counter:
    doc = ner_nlp(text)
    return Counter(ent.text for ent in doc.ents)


def normalize_to_canonical(counts: Counter, terms_df: pd.DataFrame) -> Counter:
    """Map matched surface text (case-insensitive) back to the canonical term/label from the CSV."""
    lookup = {row.term.lower(): (row.term, row.label) for row in terms_df.itertuples()}
    canonical_counts = Counter()
    label_of = {}
    for surface, n in counts.items():
        canon, label = lookup.get(surface.lower(), (surface, None))
        canonical_counts[canon] += n
        label_of[canon] = label
    return canonical_counts, label_of


def main():
    parser = argparse.ArgumentParser(description="Scan OJT documents for IT terms and count matches.")
    parser.add_argument("--input", required=True, type=Path, help="File or folder of OJT documents")
    parser.add_argument("--terms-csv", required=True, type=Path, help="CSV of predefined terms (term,label)")
    parser.add_argument("--model", type=Path, default=None, help="Path to trained NER model (for ner/hybrid modes)")
    parser.add_argument("--mode", choices=["ruler", "ner", "hybrid"], default="hybrid")
    parser.add_argument("--context-mining", choices=["on", "off"], default="on",
                         help="Mine untrained context-pattern candidates for terms not in the CSV "
                              "(e.g. 'integrated <X>'). Needs no trained model. Default: on.")
    parser.add_argument("--output", type=Path, default=Path("results"), help="Output directory")
    args = parser.parse_args()

    if args.mode in ("ner", "hybrid") and args.model is None:
        sys.exit(f"--mode {args.mode} requires --model <path to trained NER model>")

    args.output.mkdir(parents=True, exist_ok=True)

    terms_df = load_terms(args.terms_csv)
    label_by_term = {row.term: row.label for row in terms_df.itertuples()}
    known_terms_lower = set(terms_df["term"].str.lower())

    blank_nlp = spacy.blank("en")
    matcher = build_phrase_matcher(blank_nlp, terms_df) if args.mode in ("ruler", "hybrid") else None
    ner_nlp = spacy.load(args.model) if args.mode in ("ner", "hybrid") else None

    context_nlp, dep_matcher = None, None
    if args.context_mining == "on":
        context_nlp = load_context_pipeline()
        dep_matcher = build_dep_matcher(context_nlp)

    documents = collect_documents(args.input)
    print(f"Scanning {len(documents)} document(s) in mode='{args.mode}'...")

    detail_rows = []
    candidate_rows = []

    for path in documents:
        try:
            text = extract_text(path)
        except Exception as e:
            print(f"  [skip] {path.name}: could not read ({e})")
            continue

        doc_counts = Counter()
        doc_labels = {}

        if matcher is not None:
            ruler_counts = scan_with_ruler(text, blank_nlp, matcher)
            canon_counts, canon_labels = normalize_to_canonical(ruler_counts, terms_df)
            doc_counts.update(canon_counts)
            doc_labels.update(canon_labels)

        if ner_nlp is not None:
            ner_counts = scan_with_ner(text, ner_nlp)
            for surface, n in ner_counts.items():
                canon = surface
                label = label_by_term.get(surface)  # None if not in predefined CSV
                if canon in label_by_term:
                    # already covered by exact match above; ner just reinforces it, skip double count
                    continue
                if args.mode == "ner":
                    doc_counts[canon] += n
                    doc_labels[canon] = label or "CANDIDATE"
                else:  # hybrid: log as a candidate, don't fold into the official counts
                    candidate_rows.append({
                        "document": path.name, "term": canon, "count": n,
                        "source": "ner", "trigger_verb": "", "example_sentence": "",
                    })

        if context_nlp is not None:
            for cand in mine_candidates(text, context_nlp, dep_matcher, known_terms_lower):
                candidate_rows.append({
                    "document": path.name, "term": cand.text, "count": 1,
                    "source": "context_pattern", "trigger_verb": cand.trigger_verb,
                    "example_sentence": cand.sentence[:200],
                })

        for term, count in doc_counts.items():
            detail_rows.append({
                "document": path.name,
                "term": term,
                "label": doc_labels.get(term, label_by_term.get(term, "")),
                "count": count,
            })

        print(f"  {path.name}: {sum(doc_counts.values())} matches across {len(doc_counts)} distinct terms")

    detail_df = pd.DataFrame(detail_rows, columns=["document", "term", "label", "count"])
    detail_df.to_csv(args.output / "term_matches_detail.csv", index=False)

    if not detail_df.empty:
        summary_df = detail_df.pivot_table(
            index="document", columns="term", values="count", aggfunc="sum", fill_value=0
        )
        summary_df.to_csv(args.output / "term_matches_summary.csv")

        category_df = detail_df.pivot_table(
            index="document", columns="label", values="count", aggfunc="sum", fill_value=0
        )
        category_df.to_csv(args.output / "category_summary.csv")
    else:
        print("No matches found across any document.")

    if candidate_rows:
        cand_df = pd.DataFrame(candidate_rows)
        cand_df = cand_df.groupby(["term", "source"], as_index=False).agg(
            count=("count", "sum"),
            documents=("document", lambda s: s.nunique()),
            trigger_verb=("trigger_verb", lambda s: next((v for v in s if v), "")),
            example_sentence=("example_sentence", lambda s: next((v for v in s if v), "")),
        )
        cand_df = cand_df.sort_values(["documents", "count"], ascending=False)
        cand_df.to_csv(args.output / "candidate_new_terms.csv", index=False)
        print(f"\n{len(cand_df)} candidate new term(s) found but not in {args.terms_csv.name} "
              f"-> review {args.output / 'candidate_new_terms.csv'} and add worthwhile ones to the CSV.")

    print(f"\nDone. Results written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
