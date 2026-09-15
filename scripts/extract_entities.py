import sys
import os
import json
import re
import unicodedata
import pdfplumber
import spacy
import mysql.connector
from mysql.connector import Error


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_NAME = "nbsc_ojt"
DB_USER = "root"
DB_PASSWORD = ""

# Generic words should never become predefined entities.
IGNORED_ENTITY_TERMS = {"my"}


# ============================================================
# ERROR HANDLER
# ============================================================

def fail(message, details=None):
    result = {
        "success": False,
        "error": message
    }

    if details:
        result["details"] = details

    print(
        json.dumps(
            result,
            ensure_ascii=False
        )
    )

    sys.exit(1)


# ============================================================
# NORMALIZE TEXT
# ============================================================

def normalize(text):
    """
    Normalize text for matching.

    Example:
        "PHP Programming"
        "php programming"
        "PHP   Programming"
        "PHP-programming"

    become easier to compare.
    """

    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = re.sub(r"[-–—‑]", " ", text)
    text = re.sub(r"[^\w\s+#.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


# ============================================================
# DATABASE CONNECTION
# ============================================================

def connect_database():

    try:

        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        if connection.is_connected():
            return connection

        return None

    except Error as e:

        fail(
            "Unable to connect to MySQL.",
            str(e)
        )


# ============================================================
# LOAD PREDEFINED ENTITIES
# ============================================================

def load_predefined_entities(connection):

    cursor = None

    try:

        cursor = connection.cursor(
            dictionary=True
        )

        query = """
            SELECT
                id,
                entity_name,
                aliases,
                category,
                activity_type,
                it_related,
                description
            FROM predefined_entities
            WHERE LOWER(TRIM(entity_name)) <> 'my'
            ORDER BY
                CHAR_LENGTH(entity_name) DESC,
                entity_name ASC
        """

        cursor.execute(query)

        entities = cursor.fetchall()

        return entities

    finally:

        if cursor is not None:
            cursor.close()


# ============================================================
# EXTRACT TEXT FROM PDF
# ============================================================

def extract_pdf_text(pdf_path):

    full_text = []

    try:

        with pdfplumber.open(pdf_path) as pdf:

            for page_number, page in enumerate(
                pdf.pages,
                start=1
            ):

                page_text = (
                    page.extract_text()
                    or ""
                )

                page_text = page_text.strip()

                if page_text:

                    full_text.append(
                        f"--- PAGE {page_number} ---\n"
                        + page_text
                    )

    except Exception as e:

        return None, str(e)

    return (
        "\n\n".join(full_text).strip(),
        None
    )


# ============================================================
# CREATE SEARCHABLE PDF TEXT
# ============================================================

def prepare_search_text(text):

    """
    Creates a normalized version of the PDF text.

    The original PDF content is NOT changed.
    This is only used internally for matching.
    """

    return normalize(text)


# ============================================================
# TERM OCCURRENCES
# ============================================================

def find_term_occurrences(text, term):
    normalized_text = prepare_search_text(text)
    normalized_term = normalize(term)

    if not normalized_text or not normalized_term:
        return []

    if normalized_term in IGNORED_ENTITY_TERMS:
        return []

    pattern = r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)"
    return list(re.finditer(pattern, normalized_text, flags=re.IGNORECASE))


def count_term(text, term):

    return len(find_term_occurrences(text, term))


# ============================================================
# SPLIT ALIASES
# ============================================================

def split_aliases(aliases):

    if aliases is None:
        return []

    aliases = str(aliases).strip()

    if not aliases:
        return []

    # Support:
    #
    # PHP|PHP Language|PHP Programming
    #
    # PHP, PHP Language, PHP Programming
    #
    # PHP;PHP Language
    #
    # all together.

    parts = re.split(
        r"[|;,]",
        aliases
    )

    result = []

    for part in parts:

        part = part.strip()

        if part:
            result.append(part)

    return result


# ============================================================
# BUILD SEARCH TERMS
# ============================================================

def build_entity_terms(entity):

    terms = []

    canonical_name = str(
        entity.get("entity_name")
        or ""
    ).strip()

    if canonical_name:
        terms.append(
            canonical_name
        )

    aliases = split_aliases(
        entity.get("aliases")
    )

    terms.extend(
        aliases
    )

    # Remove duplicate normalized values
    unique_terms = []

    seen = set()

    for term in terms:

        normalized_term = normalize(term)

        if not normalized_term or normalized_term in IGNORED_ENTITY_TERMS:
            continue

        if normalized_term in seen:
            continue

        seen.add(
            normalized_term
        )

        unique_terms.append(
            term
        )

    return sorted(
        unique_terms,
        key=lambda term: (-len(normalize(term)), normalize(term))
    )


# ============================================================
# FIND PREDEFINED ENTITY MATCHES
# ============================================================

def find_predefined_matches(
    text,
    predefined_entities
):

    matches = []

    for entity in predefined_entities:

        canonical_name = str(entity.get("entity_name") or "").strip()
        if normalize(canonical_name) in IGNORED_ENTITY_TERMS:
            continue

        entity_terms = build_entity_terms(
            entity
        )

        if not entity_terms:
            continue

        best_term = ""
        best_frequency = 0
        best_term_length = 0

        # ----------------------------------------------------
        # Check canonical name and aliases
        # ----------------------------------------------------

        for term in entity_terms:

            occurrences = find_term_occurrences(text, term)
            frequency = len(occurrences)
            term_length = len(normalize(term))

            # Prefer the longer verified term when frequencies tie.
            if (
                frequency > best_frequency
                or (
                    frequency == best_frequency
                    and term_length > best_term_length
                )
            ):
                best_frequency = frequency
                best_term = term
                best_term_length = term_length

        # ----------------------------------------------------
        # Only return entities actually found
        # in the PDF.
        # ----------------------------------------------------

        if best_frequency <= 0:
            continue

        entity_name = str(
            entity.get("entity_name")
            or best_term
        ).strip()

        canonical_name = entity_name

        category = str(
            entity.get("category")
            or "Other"
        ).strip()

        activity_type = str(
            entity.get("activity_type")
            or "Other"
        ).strip()

        it_related = str(
            entity.get("it_related")
            or "unknown"
        ).strip().lower()

        # ----------------------------------------------------
        # Validate activity type
        # ----------------------------------------------------

        allowed_activity_types = [
            "Software",
            "Hardware",
            "Clerical",
            "Other"
        ]

        if activity_type not in allowed_activity_types:

            activity_type = "Other"

        # ----------------------------------------------------
        # Validate IT related
        # ----------------------------------------------------

        if it_related not in [
            "yes",
            "no",
            "unknown"
        ]:

            it_related = "unknown"

        # ----------------------------------------------------
        # Add matched entity
        # ----------------------------------------------------

        matches.append({

            "predefined_id":
                int(entity["id"]),

            "entity":
                entity_name,

            "entity_name":
                entity_name,

            "canonical_name":
                canonical_name,

            "category":
                category,

            "activity_type":
                activity_type,

            "it_related":
                it_related,

            "description":
                entity.get("description"),

            "matched_term":
                best_term,

            "frequency":
                best_frequency,

            "source":
                "predefined"

        })

    return matches


def resolve_entity_overlaps(text, matches):
    """
    Keep only verified, non-overlapping occurrences across all predefined
    entities. Longer terms win, so Windows 11 is preferred over Windows
    for the same occurrence, while a separate standalone Windows remains.
    """
    candidates = []
    for entity_index, match in enumerate(matches):
        term = normalize(match.get("matched_term", ""))
        if not term or term in IGNORED_ENTITY_TERMS:
            continue

        for occurrence in find_term_occurrences(text, term):
            candidates.append({
                "entity_index": entity_index,
                "start": occurrence.start(),
                "end": occurrence.end(),
                "length": occurrence.end() - occurrence.start()
            })

    candidates.sort(
        key=lambda item: (
            -item["length"],
            item["start"],
            item["entity_index"]
        )
    )

    accepted = []
    frequencies = {}

    for candidate in candidates:
        overlaps = any(
            candidate["start"] < span["end"]
            and candidate["end"] > span["start"]
            for span in accepted
        )
        if overlaps:
            continue

        accepted.append(candidate)
        index = candidate["entity_index"]
        frequencies[index] = frequencies.get(index, 0) + 1

    verified = []
    for index, match in enumerate(matches):
        frequency = frequencies.get(index, 0)
        if frequency == 0:
            continue

        verified_match = dict(match)
        verified_match["frequency"] = frequency
        verified.append(verified_match)

    return verified


# ============================================================
# SPAcy ENTITIES
# ============================================================

def extract_spacy_entities(doc):

    entities = []

    seen = set()

    for ent in doc.ents:

        entity_text = (
            ent.text
            .strip()
        )

        if not entity_text:
            continue

        key = (
            normalize(entity_text),
            ent.label_
        )

        if key in seen:
            continue

        seen.add(key)

        entities.append({

            "text":
                entity_text,

            "label":
                ent.label_

        })

    return entities


# ============================================================
# CALCULATE SUMMARY
# ============================================================

def calculate_summary(entities):

    software_count = 0
    hardware_count = 0
    clerical_count = 0
    other_count = 0

    it_related_count = 0
    non_it_count = 0

    total_frequency = 0

    for entity in entities:

        try:

            frequency = int(
                entity.get(
                    "frequency",
                    1
                )
            )

        except Exception:

            frequency = 1

        if frequency < 1:
            frequency = 1

        total_frequency += frequency

        # ----------------------------------------------------
        # Activity type
        # ----------------------------------------------------

        activity_type = str(
            entity.get(
                "activity_type",
                ""
            )
        ).strip().lower()

        if activity_type == "software":

            software_count += frequency

        elif activity_type == "hardware":

            hardware_count += frequency

        elif activity_type == "clerical":

            clerical_count += frequency

        else:

            other_count += frequency

        # ----------------------------------------------------
        # IT related
        # ----------------------------------------------------

        it_related = str(
            entity.get(
                "it_related",
                ""
            )
        ).strip().lower()

        if it_related == "yes":

            it_related_count += frequency

        elif it_related == "no":

            non_it_count += frequency

    # --------------------------------------------------------
    # IT / Clerical percentage
    # --------------------------------------------------------

    classified_total = (
        it_related_count
        + non_it_count
    )

    if classified_total > 0:

        it_percentage = round(
            (
                it_related_count
                / classified_total
            ) * 100,
            2
        )

        clerical_percentage = round(
            (
                non_it_count
                / classified_total
            ) * 100,
            2
        )

    else:

        it_percentage = 0
        clerical_percentage = 0

    # --------------------------------------------------------
    # Activity percentages
    # --------------------------------------------------------

    if total_frequency > 0:

        software_percentage = round(
            (
                software_count
                / total_frequency
            ) * 100,
            2
        )

        hardware_percentage = round(
            (
                hardware_count
                / total_frequency
            ) * 100,
            2
        )

        clerical_activity_percentage = round(
            (
                clerical_count
                / total_frequency
            ) * 100,
            2
        )

        other_percentage = round(
            (
                other_count
                / total_frequency
            ) * 100,
            2
        )

    else:

        software_percentage = 0
        hardware_percentage = 0
        clerical_activity_percentage = 0
        other_percentage = 0

    return {

        "total":
            total_frequency,

        "unique_entities":
            len(entities),

        "it_related":
            it_related_count,

        "clerical":
            non_it_count,

        "it_percentage":
            it_percentage,

        "clerical_percentage":
            clerical_percentage,

        "software":
            software_count,

        "hardware":
            hardware_count,

        "clerical_activity":
            clerical_count,

        "other":
            other_count,

        "software_percentage":
            software_percentage,

        "hardware_percentage":
            hardware_percentage,

        "clerical_activity_percentage":
            clerical_activity_percentage,

        "other_percentage":
            other_percentage
    }


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # 1. GET PDF PATH
    # ========================================================

    if len(sys.argv) < 2:

        fail(
            "No PDF path was provided."
        )

    pdf_path = os.path.abspath(
        sys.argv[1]
    )

    # ========================================================
    # 2. VERIFY PDF
    # ========================================================

    if not os.path.isfile(pdf_path):

        fail(
            "PDF file not found.",
            pdf_path
        )

    # ========================================================
    # 3. LOAD spaCy
    # ========================================================

    try:

        nlp = spacy.load(
            "en_core_web_sm"
        )

    except Exception as e:

        fail(
            "Failed to load spaCy model 'en_core_web_sm'.",
            str(e)
        )

    # ========================================================
    # 4. EXTRACT PDF TEXT
    # ========================================================

    text, pdf_error = (
        extract_pdf_text(
            pdf_path
        )
    )

    if pdf_error:

        fail(
            "Failed to read PDF.",
            pdf_error
        )

    if not text:

        fail(
            "No extractable text was found in the PDF."
        )

    # ========================================================
    # 5. PROCESS PDF WITH spaCy
    # ========================================================

    try:

        doc = nlp(text)

    except Exception as e:

        fail(
            "spaCy processing failed.",
            str(e)
        )

    # ========================================================
    # 6. GET spaCy ENTITIES
    # ========================================================

    spacy_entities = (
        extract_spacy_entities(
            doc
        )
    )

    # ========================================================
    # 7. CONNECT DATABASE
    # ========================================================

    connection = None

    try:

        connection = connect_database()

        if connection is None:

            fail(
                "Unable to connect to database."
            )

        # ====================================================
        # 8. LOAD PREDEFINED ENTITIES
        # ====================================================

        predefined_entities = (
            load_predefined_entities(
                connection
            )
        )

        # ====================================================
        # 9. MATCH PDF AGAINST DATABASE
        # ====================================================

        matched_entities = (
            find_predefined_matches(
                text,
                predefined_entities
            )
        )

        matched_entities = resolve_entity_overlaps(
            text,
            matched_entities
        )

        # ====================================================
        # 10. CALCULATE SUMMARY
        # ====================================================

        summary = calculate_summary(
            matched_entities
        )

        # ====================================================
        # 11. RETURN JSON TO PHP
        # ====================================================

        result = {

            "success":
                True,

            "content":
                text,

            # Raw spaCy entities are returned only
            # for debugging / verification.
            "spacy_entities":
                spacy_entities,

            # These are the ONLY entities that
            # should be displayed by the PHP page.
            "entities":
                matched_entities,

            "predefined_matches":
                matched_entities,

            "entity_count":
                len(matched_entities),

            "summary":
                summary

        }

        print(
            json.dumps(
                result,
                ensure_ascii=False
            )
        )

    except Error as e:

        fail(
            "Database error while loading predefined entities.",
            str(e)
        )

    except Exception as e:

        fail(
            "Entity matching failed.",
            str(e)
        )

    finally:

        if connection is not None:

            try:

                if connection.is_connected():

                    connection.close()

            except Exception:

                pass


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()