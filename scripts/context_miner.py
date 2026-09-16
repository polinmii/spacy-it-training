"""
context_miner.py
-----------------
Finds *candidate* IT terms from syntactic context, independent of the predefined
CSV list or any trained model. Targets patterns like:

    "...I integrated Stripe API into the system"
    "...deployed the app using Kubernetes and Terraform"
    "we migrated to PostgreSQL"
    "worked with Django and React"

i.e. TRIGGER_VERB (+ preposition) -> OBJECT noun phrase. The object noun phrase is
returned as a candidate term, whether or not it's already in your CSV.

This needs a dependency parser, so it uses `en_core_web_sm` (or any pipeline with
tagger+parser) rather than a blank pipeline:
    python -m spacy download en_core_web_sm

Two uses:
  1. Training-time: enrich NER training data with a generic TECH_TERM label so the
     trained model learns the *pattern* ("things after these verbs tend to be tech
     terms"), not just the CSV vocabulary -- this is what lets it generalize to
     genuinely new/unlisted terms.
  2. Deployment-time: run standalone (no trained model needed) to surface brand-new
     terms in new submissions for human review, independent of whatever the NER
     model did or didn't learn.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import spacy
from spacy.matcher import DependencyMatcher
from spacy.tokens import Doc, Span

# Verbs commonly used by students describing tools/tech they used during OJT.
# Extend this list based on what you see in your own documents.
TRIGGER_LEMMAS = [
    "integrate", "use", "utilize", "utilise", "implement", "deploy", "adopt",
    "leverage", "employ", "build", "develop", "configure", "setup", "migrate",
    "install", "learn", "train", "test", "apply", "handle", "write", "create",
    "maintain", "manage", "automate", "connect", "query", "host", "containerize",
    "run", "debug", "work",
]

# Action verbs characteristic of IT / technical tasks
IT_TRIGGER_LEMMAS = {
    "troubleshoot", "debug", "configure", "assemble", "disassemble", "install",
    "code", "develop", "deploy", "program", "format", "reset", "bypass", "test",
    "design", "encrypt", "backup", "restore", "migrate", "integrate", "build",
    "maintain", "update", "patch", "setup", "wire", "repair", "clean", "scan",
    "host", "query", "automate", "digitalize", "optimize", "commit", "push",
}

# Action verbs characteristic of clerical / administrative tasks
CLERICAL_TRIGGER_LEMMAS = {
    "organize", "arrange", "file", "sort", "stamp", "photocopy", "print",
    "distribute", "transcribe", "route", "refill", "laminate", "escort",
    "compile", "receive", "deliver", "schedule", "coordinate", "log", "record",
    "release", "shred", "count", "dispense", "attend", "participate", "usher",
}

# Core IT domain nouns that signal technical context
IT_DOMAIN_NOUNS = {
    "hardware", "pc", "computer", "laptop", "system", "code", "database",
    "network", "wifi", "lan", "server", "ui", "ux", "frontend", "backend",
    "ssd", "hdd", "drive", "ram", "cpu", "monitor", "driver", "git", "github",
    "repository", "endpoint", "api", "application", "firewall", "router", "cable",
    "ethernet", "software", "script", "terminal", "screen", "keyboard", "mouse",
    "printer", "emulator", "prototype", "module", "bug", "patch", "flashdrive",
    "port", "vlan", "excel", "sheets", "photoshop", "canva", "figma",
}

# Core clerical domain nouns that signal office / paperwork context
CLERICAL_DOMAIN_NOUNS = {
    "document", "documents", "paper", "papers", "hardcopy", "hardcopies", "folder",
    "folders", "cabinet", "cabinets", "binder", "binders", "clearance", "permit",
    "permits", "passbook", "passbooks", "certificate", "certificates", "sachet",
    "sachets", "medicine", "medicines", "prescription", "prescriptions", "flyer",
    "flyers", "brochure", "brochures", "attendance", "receipt", "receipts",
    "envelope", "envelopes", "form", "forms", "signatory", "signatories",
    "record", "records", "voucher", "notice", "poster", "label", "labels",
    "stall", "visitor", "id", "ids", "slip", "slips", "meeting", "minutes",
}

# Only these prepositions signal a tool/tech relationship ("worked WITH Docker",
# "deployed ON AWS"). Others like "during"/"for"/"since" are usually temporal and
# just add noise, so they're deliberately excluded.
RELEVANT_PREPS = {"with", "using", "on", "in", "via", "through", "to", "into", "onto", "from"}

# Generic nouns that often follow those verbs but are NOT tech terms
# ("integrated the system", "used my skills"). Filtered out during scoring.
GENERIC_STOPWORDS = {
    "system", "systems", "project", "projects", "team", "teams", "process",
    "processes", "task", "tasks", "skill", "skills", "tool", "tools", "data",
    "report", "reports", "documentation", "application", "applications", "app",
    "apps", "website", "websites", "program", "programs", "code", "feature",
    "features", "requirement", "requirements", "environment", "solution",
    "solutions", "software", "hardware", "company", "client", "clients",
    "customer", "customers", "issue", "issues", "problem", "problems",
    "training", "internship", "knowledge", "experience", "workflow", "modules",
    "module", "changes", "updates", "files", "file", "version",
}

_CAMEL_RE = re.compile(r"^[a-z]+[A-Z]")
_HAS_DIGIT_RE = re.compile(r"\d")


def build_dep_matcher(nlp: spacy.language.Language) -> DependencyMatcher:
    """Two patterns: VERB -> direct object, and VERB -> prep -> object of prep."""
    matcher = DependencyMatcher(nlp.vocab)

    # NOTE: object nodes are constrained to NOUN/PROPN so that verb-verb
    # coordination ("integrated X and used Y" -- "used" is dep=conj of
    # "integrated") never gets picked up as if "used" were the object.
    direct_pattern = [
        {"RIGHT_ID": "verb", "RIGHT_ATTRS": {"LEMMA": {"IN": TRIGGER_LEMMAS}, "POS": "VERB"}},
        {"LEFT_ID": "verb", "REL_OP": ">", "RIGHT_ID": "object",
         "RIGHT_ATTRS": {"DEP": {"IN": ["dobj", "attr", "oprd", "conj"]}, "POS": {"IN": ["NOUN", "PROPN"]}}},
    ]
    prep_pattern = [
        {"RIGHT_ID": "verb", "RIGHT_ATTRS": {"LEMMA": {"IN": TRIGGER_LEMMAS}, "POS": "VERB"}},
        {"LEFT_ID": "verb", "REL_OP": ">", "RIGHT_ID": "prep",
         "RIGHT_ATTRS": {"DEP": "prep", "LOWER": {"IN": sorted(RELEVANT_PREPS)}}},
        {"LEFT_ID": "prep", "REL_OP": ">", "RIGHT_ID": "object",
         "RIGHT_ATTRS": {"DEP": "pobj", "POS": {"IN": ["NOUN", "PROPN"]}}},
    ]
    matcher.add("TRIGGER_DIRECT_OBJECT", [direct_pattern])
    matcher.add("TRIGGER_PREP_OBJECT", [prep_pattern])
    return matcher


def _looks_like_tech_term(text: str) -> bool:
    """Heuristic filter: capitalized / ALLCAPS / camelCase / has-digit tokens are
    treated as likely product/tool names; plain lowercase generic nouns are not."""
    text = text.strip()
    if not text or text.lower() in GENERIC_STOPWORDS:
        return False
    if len(text.split()) > 4:
        return False
    if _HAS_DIGIT_RE.search(text):
        return True
    if _CAMEL_RE.match(text.replace(" ", "")):
        return True
    tokens = [t for t in text.split() if t.isalpha()]
    if any(t.isupper() and len(t) >= 2 for t in tokens):  # acronym: AWS, SQL
        return True
    if any(t[:1].isupper() for t in tokens):  # Title Case: Django, Stripe
        return True
    return False


def _span_for_token(doc: Doc, token) -> Span:
    for chunk in doc.noun_chunks:
        if chunk.start <= token.i < chunk.end:
            return _trim_leading_det(chunk)
    return doc[token.i:token.i + 1]


def _trim_leading_det(span: Span) -> Span:
    """Noun chunks include leading determiners/possessives ('a GraphQL endpoint',
    'my Jira board'); drop those so the candidate term itself stays clean."""
    start = span.start
    while start < span.end and span.doc[start].pos_ in {"DET", "PRON"}:
        start += 1
    if start >= span.end:
        return span
    return span.doc[start:span.end]


@dataclass
class Candidate:
    text: str
    trigger_verb: str
    sentence: str


def mine_candidates(
    text: str,
    nlp: spacy.language.Language,
    matcher: DependencyMatcher,
    known_terms_lower: Optional[set] = None,
) -> list:
    """Return candidate tech-term spans following trigger verbs in `text`,
    excluding terms already in `known_terms_lower` (e.g. your CSV, lowercased)
    and generic/non-tech-looking nouns. One candidate per unique surface form
    per document (first occurrence's sentence is kept as the example)."""
    known_terms_lower = known_terms_lower or set()
    doc = nlp(text)
    results = []
    seen = set()

    for match_id, token_ids in matcher(doc):
        verb_tok = doc[token_ids[0]]
        obj_tok = doc[token_ids[-1]]
        span = _span_for_token(doc, obj_tok)
        span_text = span.text.strip(" .,;:\"'()")

        if not _looks_like_tech_term(span_text):
            continue
        key = span_text.lower()
        if key in known_terms_lower or key in seen:
            continue
        seen.add(key)
        results.append(Candidate(text=span_text, trigger_verb=verb_tok.lemma_, sentence=span.sent.text.strip()))

    return results


def load_context_pipeline(model_name: str = "en_core_web_sm") -> spacy.language.Language:
    """Loads a pipeline with tagger+parser (required for dependency-based mining)."""
    try:
        return spacy.load(model_name)
    except OSError as e:
        raise OSError(
            f"Context-pattern mining needs a parser model. Run: python -m spacy download {model_name}"
        ) from e


@dataclass
class TaskPatternCandidate:
    sentence: str
    predicted_label: str
    confidence: float
    it_score: float
    clerical_score: float
    matched_verbs: list[str]
    matched_nouns: list[str]


def classify_task_heuristic(text: str, nlp: Optional[spacy.language.Language] = None) -> dict[str, float]:
    """Pattern-based heuristic classifier for unknown tasks.
    Scores sentences using syntactic dependency cues, action verbs, and domain nouns.
    Returns probability distribution: {'IT_TASK': float, 'CLERICAL': float}."""
    if nlp is not None:
        doc = nlp(text)
    else:
        words = set(re.findall(r"\b[a-zA-Z0-9_\-]+\b", text.lower()))
        it_score = sum(2.0 for w in words if w in IT_TRIGGER_LEMMAS) + sum(1.5 for w in words if w in IT_DOMAIN_NOUNS)
        cl_score = sum(2.0 for w in words if w in CLERICAL_TRIGGER_LEMMAS) + sum(1.5 for w in words if w in CLERICAL_DOMAIN_NOUNS)
        diff = it_score - cl_score
        prob_it = 1.0 / (1.0 + math.exp(-diff)) if abs(diff) > 0 else 0.5
        return {"IT_TASK": round(prob_it, 3), "CLERICAL": round(1.0 - prob_it, 3)}

    it_score = 0.0
    cl_score = 0.0

    for token in doc:
        lemma = token.lemma_.lower()
        text_lower = token.text.lower()

        # Verb cues (higher weight when functioning as verb/predicate)
        if token.pos_ == "VERB" or token.dep_ in ("ROOT", "xcomp", "advcl", "conj"):
            if lemma in IT_TRIGGER_LEMMAS:
                it_score += 2.0
            if lemma in CLERICAL_TRIGGER_LEMMAS:
                cl_score += 2.0

        # Domain nouns & objects
        if lemma in IT_DOMAIN_NOUNS or text_lower in IT_DOMAIN_NOUNS:
            it_score += 1.5
        if lemma in CLERICAL_DOMAIN_NOUNS or text_lower in CLERICAL_DOMAIN_NOUNS:
            cl_score += 1.5

    diff = it_score - cl_score
    prob_it = 1.0 / (1.0 + math.exp(-diff)) if abs(diff) > 0 else 0.5
    return {"IT_TASK": round(prob_it, 3), "CLERICAL": round(1.0 - prob_it, 3)}


def mine_task_patterns(text: str, nlp: spacy.language.Language) -> list[TaskPatternCandidate]:
    """Scans freeform text for task-like sentences or clauses and classifies them
    using syntactic dependency patterns."""
    doc = nlp(text)
    results = []

    for sent in doc.sents:
        sent_text = sent.text.strip()
        if len(sent_text.split()) < 3:
            continue

        matched_verbs = []
        matched_nouns = []
        it_score = 0.0
        cl_score = 0.0

        for token in sent:
            lemma = token.lemma_.lower()
            text_lower = token.text.lower()

            if token.pos_ == "VERB" or token.dep_ in ("ROOT", "xcomp", "advcl", "conj"):
                if lemma in IT_TRIGGER_LEMMAS:
                    matched_verbs.append(f"{lemma} (IT)")
                    it_score += 2.0
                elif lemma in CLERICAL_TRIGGER_LEMMAS:
                    matched_verbs.append(f"{lemma} (CLERICAL)")
                    cl_score += 2.0

            if lemma in IT_DOMAIN_NOUNS or text_lower in IT_DOMAIN_NOUNS:
                matched_nouns.append(f"{text_lower} (IT)")
                it_score += 1.5
            elif lemma in CLERICAL_DOMAIN_NOUNS or text_lower in CLERICAL_DOMAIN_NOUNS:
                matched_nouns.append(f"{text_lower} (CLERICAL)")
                cl_score += 1.5

        if not matched_verbs and not matched_nouns:
            continue

        diff = it_score - cl_score
        prob_it = 1.0 / (1.0 + math.exp(-diff)) if abs(diff) > 0 else 0.5
        pred_label = "IT_TASK" if prob_it >= 0.5 else "CLERICAL"
        confidence = prob_it if pred_label == "IT_TASK" else (1.0 - prob_it)

        results.append(
            TaskPatternCandidate(
                sentence=sent_text,
                predicted_label=pred_label,
                confidence=round(confidence, 3),
                it_score=it_score,
                clerical_score=cl_score,
                matched_verbs=matched_verbs,
                matched_nouns=matched_nouns,
            )
        )

    return results
