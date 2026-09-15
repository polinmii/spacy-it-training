#!/usr/bin/env python3
"""
Test script for deploy_term_scanner.py
--------------------------------------
Picks 10 files from Temploads/, runs the scanner in hybrid mode,
and prints a summary of results + any issues found.

Usage:
    python test_deploy_scanner.py
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

# ── Config ──
SCRIPTS_DIR = Path(__file__).resolve().parent
WORKSPACE   = SCRIPTS_DIR.parent              # project root (one level up from scripts/)
TERMS_CSV   = WORKSPACE / "models" / "it_terms.csv"
NER_MODEL   = WORKSPACE / "models" / "it_term_ner"
SCANNER     = SCRIPTS_DIR / "deploy_term_scanner.py"
TEST_DIR    = WORKSPACE / "uploads" / "test"
RESULTS_DIR = WORKSPACE / "_test_results"
PYTHON      = WORKSPACE / ".venv" / "bin" / "python"
NUM_FILES   = 15


def pick_test_files():
    """Pick the N smallest files from Temploads for a fast test."""
    files = sorted(
        (p for p in TEST_DIR.iterdir() if p.suffix.lower() in {".txt", ".docx", ".pdf"}),
        key=lambda p: p.stat().st_size,
    )
    return files


def run_scanner():
    """Run the deploy scanner and return (returncode, stdout, stderr, elapsed)."""
    cmd = [
        str(PYTHON), str(SCANNER),
        "--input", str(TEST_DIR),
        "--terms-csv", str(TERMS_CSV),
        "--model", str(NER_MODEL),
        "--mode", "hybrid",
        "--output", str(RESULTS_DIR),
    ]
    print(f"Running: {' '.join(cmd[-10:])}")
    print("=" * 70)

    start = time.time()
    proc = subprocess.run(cmd, capture_output=False, text=True)
    elapsed = time.time() - start

    return proc.returncode, elapsed


def print_results(returncode, elapsed):
    """Read the output CSVs and print a summary."""
    print()
    print("=" * 70)
    print(f"Scanner exited with code {returncode} in {elapsed:.1f}s")
    print("=" * 70)

    if returncode != 0:
        print("❌ Scanner FAILED — check the output above for errors.")
        return False

    # ── Detail CSV ──
    detail_path = RESULTS_DIR / "term_matches_detail.csv"
    if detail_path.exists():
        df = pd.read_csv(detail_path)
        print(f"\n📄 term_matches_detail.csv: {len(df)} rows")
        if not df.empty:
            print(f"   Documents with matches: {df['document'].nunique()}")
            print(f"   Distinct terms found:   {df['term'].nunique()}")
            print(f"   Total match count:      {df['count'].sum()}")
            print(f"\n   Top 15 terms by count:")
            top = df.groupby("term")["count"].sum().sort_values(ascending=False).head(15)
            for term, count in top.items():
                print(f"     {count:4d}x  {term}")
        else:
            print("   ⚠️  No matches found — check if PDFs are extractable")
    else:
        print("   ⚠️  term_matches_detail.csv not created")

    # ── Summary CSV ──
    summary_path = RESULTS_DIR / "term_matches_summary.csv"
    if summary_path.exists():
        sdf = pd.read_csv(summary_path)
        print(f"\n📊 term_matches_summary.csv: {len(sdf)} documents × {len(sdf.columns)-1} terms")
    else:
        print("\n   ⚠️  term_matches_summary.csv not created")

    # ── Category CSV ──
    cat_path = RESULTS_DIR / "category_summary.csv"
    if cat_path.exists():
        cdf = pd.read_csv(cat_path)
        print(f"\n🏷️  category_summary.csv: {len(cdf)} documents × {len(cdf.columns)-1} categories")
        # Show category totals
        num_cols = cdf.select_dtypes(include="number").columns
        if len(num_cols):
            totals = cdf[num_cols].sum().sort_values(ascending=False)
            print("   Category totals:")
            for cat, total in totals.items():
                print(f"     {int(total):4d}x  {cat}")
    else:
        print("\n   ⚠️  category_summary.csv not created")

    # ── Candidates CSV ──
    cand_path = RESULTS_DIR / "candidate_new_terms.csv"
    if cand_path.exists():
        candf = pd.read_csv(cand_path)
        print(f"\n🔍 candidate_new_terms.csv: {len(candf)} candidate terms NOT in the CSV")
        if not candf.empty:
            print("   Top 10 candidates:")
            for _, row in candf.head(10).iterrows():
                print(f"     [{row.get('source','')}] {row['term']} "
                      f"(count={row.get('count','?')}, docs={row.get('documents','?')})")
    else:
        print("\n   (No candidate_new_terms.csv — no new terms discovered, which is normal for few docs)")

    print(f"\n📁 Full results in: {RESULTS_DIR.resolve()}")
    return True


def main():
    # ── Preflight checks ──
    errors = []
    if not TEST_DIR.exists():
        errors.append(f"Test directory not found at {TEST_DIR}")
    if not TERMS_CSV.exists():
        errors.append(f"it_terms.csv not found at {TERMS_CSV}")
    if not NER_MODEL.exists():
        errors.append(f"NER model not found at {NER_MODEL}")
    if not SCANNER.exists():
        errors.append(f"Scanner script not found at {SCANNER}")
    if not PYTHON.exists():
        errors.append(f"Python venv not found at {PYTHON}")
    if errors:
        for e in errors:
            print(f"❌ {e}")
        sys.exit(1)

    # ── Pick files ──
    files = pick_test_files()
    print(f"Selected {len(files)} test files (smallest from Temploads/):")
    for f in files:
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  {size_mb:6.1f} MB  {f.name}")
    print()

    # ── Setup ──
    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)

    # ── Run ──
    returncode, elapsed = run_scanner()

    # ── Report ──
    ok = print_results(returncode, elapsed)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
