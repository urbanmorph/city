#!/usr/bin/env python3
"""
extract_budget.py — Extract budget data from Bengaluru corporation budget PDFs.

Reads municipal budget PDFs (NMAM format) and outputs structured JSON
matching the format used by the city governance portal.

Expected PDF structure (NMAM — National Municipal Accounts Manual):
    - Multiple tables per PDF, split across revenue and capital sections.
    - Each table has a header row with columns like:
        Head of Account | Code | 2024-25 Actuals | 2025-26 RE | 2026-27 BE
    - Amounts are in Indian number format with lakh-style commas (e.g. 1,23,456).
    - Revenue section covers tax receipts, grants, fees, etc.
    - Expenditure section covers departmental spending by function.
    - Capital section covers asset creation, loans, investments.

Data source:
    Budget PDFs for the 5 Bengaluru corporations (2026-27) are expected to be
    published on data.opencity.in. Download them into:
        data/raw/bengaluru/budgets/2026-27/

Usage:
    # Process all PDFs in a directory
    python3 scripts/extract_budget.py -i data/raw/bengaluru/budgets/2026-27/

    # Process a single PDF
    python3 scripts/extract_budget.py -i data/raw/bengaluru/budgets/2026-27/central.pdf -c central

    # Custom output directory and fiscal year
    python3 scripts/extract_budget.py -i input/ -y 2026-27 -o data/bengaluru/budgets/2026-27/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import pdfplumber
except ImportError:
    print(
        "ERROR: pdfplumber is required but not installed.\n"
        "Install it with: pip install pdfplumber\n"
        "Or: pip install -r scripts/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CORPORATIONS = {"central", "south", "east", "west", "north"}

# Repo root is one level above the scripts/ directory
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FUNCTION_MAPPING = REPO_ROOT / "data" / "bengaluru" / "function-mapping.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bengaluru" / "budgets" / "2026-27"
DEFAULT_YEAR = "2026-27"

# Patterns for inferring corporation ID from PDF filenames
# Matches: central.pdf, bengaluru_central_2026-27.pdf, BBMP_Central_Budget.pdf, etc.
# Note: \b treats _ as a word char, so we use lookaround for non-alpha boundaries.
CORP_FILENAME_PATTERNS = [
    re.compile(rf"(?<![a-zA-Z])({corp})(?![a-zA-Z])", re.IGNORECASE)
    for corp in VALID_CORPORATIONS
]

# Minimum number of numeric-looking cells in a row to consider it a data row
MIN_NUMERIC_COLS = 2

# Column header keywords that signal a budget table
BUDGET_TABLE_HEADER_KEYWORDS = [
    "budget estimate", "revised estimate", "actuals", "head of account",
    "account code", "particulars", "budget head", "receipts", "expenditure",
    "code no", "major head", "minor head", "sub head",
]

# Revenue vs expenditure section markers (case-insensitive)
REVENUE_MARKERS = ["revenue account", "revenue receipts", "revenue income"]
EXPENDITURE_MARKERS = ["revenue expenditure", "expenditure", "capital expenditure"]


# ---------------------------------------------------------------------------
# Indian number parsing
# ---------------------------------------------------------------------------

def parse_indian_number(text: str | None) -> float | None:
    """Parse an Indian-format number string into a float.

    Indian numbering uses lakh-style grouping: 1,23,45,678
    Also handles parenthesized negatives: (1,234) -> -1234

    Examples:
        >>> parse_indian_number("1,23,456")
        123456.0
        >>> parse_indian_number("(5,000)")
        -5000.0
        >>> parse_indian_number("12.50")
        12.5
        >>> parse_indian_number("-")
        None
        >>> parse_indian_number(None)
        None
    """
    if text is None:
        return None

    text = str(text).strip()

    # Empty, dash, or purely non-numeric
    if not text or text in ("-", "–", "—", "nil", "Nil", "NIL", "...", "N/A", "n/a"):
        return None

    # Detect negative from parentheses: (1,234)
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    # Detect negative from leading minus
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()

    # Remove all commas (Indian or Western grouping)
    text = text.replace(",", "")

    # Remove currency symbols and whitespace
    text = text.replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()

    # Try to parse
    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Function mapping
# ---------------------------------------------------------------------------

def load_function_mapping(path: Path) -> dict[str, list[str]]:
    """Load function-mapping.json and return {function_id: [keywords]}.

    Each function has a list of budget_heads (slugs). We expand these into
    plain keywords for matching against extracted budget head text. For example,
    "street-lighting" becomes ["street", "lighting"], and "swm" stays as ["swm"].

    Returns:
        dict mapping function_id -> list of lowercase keyword strings
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mapping: dict[str, list[str]] = {}
    for func in data.get("functions", []):
        func_id = func["id"]
        keywords: list[str] = []
        for head in func.get("budget_heads", []):
            # Split slug on hyphens to get individual keywords
            parts = head.lower().replace("-", " ").split()
            keywords.extend(parts)
        # Also add the function name words as fallback keywords
        for word in func.get("name", "").lower().split():
            if len(word) > 2 and word not in keywords:
                keywords.append(word)
        mapping[func_id] = keywords

    return mapping


def match_function(text: str, function_mapping: dict[str, list[str]]) -> str | None:
    """Match a budget head text to a function ID using keyword matching.

    Uses a simple scoring approach: for each function, count how many of its
    keywords appear in the text. The function with the highest score wins,
    provided at least one keyword matched.

    Args:
        text: The budget head / line item description text.
        function_mapping: Output of load_function_mapping().

    Returns:
        The best-matching function_id, or None if no keywords match.
    """
    if not text:
        return None

    text_lower = text.lower()
    best_id: str | None = None
    best_score = 0

    for func_id, keywords in function_mapping.items():
        score = 0
        for kw in keywords:
            # Use word-boundary-ish matching: keyword appears as a
            # substring surrounded by non-alpha characters or string edges
            pattern = rf"(?<![a-z]){re.escape(kw)}(?![a-z])"
            if re.search(pattern, text_lower):
                score += 1
        if score > best_score:
            best_score = score
            best_id = func_id

    return best_id


# ---------------------------------------------------------------------------
# Table detection and extraction
# ---------------------------------------------------------------------------

def is_budget_table_header(row: list[str | None]) -> bool:
    """Check whether a row looks like a budget table header.

    Looks for header keyword matches in the concatenated cell text.
    """
    combined = " ".join(str(cell or "") for cell in row).lower()
    matches = sum(1 for kw in BUDGET_TABLE_HEADER_KEYWORDS if kw in combined)
    return matches >= 1


def is_data_row(row: list[str | None]) -> bool:
    """Check whether a row looks like a data row (has enough numeric cells)."""
    numeric_count = sum(1 for cell in row if parse_indian_number(cell) is not None)
    return numeric_count >= MIN_NUMERIC_COLS


def classify_columns(header_row: list[str | None]) -> dict[str, int]:
    """Try to identify which column indices correspond to which fields.

    Returns a dict with possible keys:
        'head'    -> index of the budget head / description column
        'code'    -> index of the account code column
        'actuals' -> index of actuals column (oldest year)
        're'      -> index of revised estimate column
        'be'      -> index of budget estimate column (current year)

    This is heuristic and may not find all columns.
    """
    result: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        text = str(cell or "").lower().strip()
        if not text:
            continue
        if any(kw in text for kw in ("head of account", "particulars", "head", "description", "budget head")):
            if "head" not in result:
                result["head"] = i
        elif any(kw in text for kw in ("code", "code no", "account code", "a/c code")):
            if "code" not in result:
                result["code"] = i
        elif "actual" in text:
            result["actuals"] = i
        elif "revised" in text or "re " in text or text.endswith(" re"):
            result["re"] = i
        elif "budget estimate" in text or "be " in text or text.endswith(" be"):
            result["be"] = i

    return result


def extract_tables_from_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract budget tables from a single PDF using pdfplumber.

    Returns a list of table dicts, each with:
        - id: str (e.g. "table_p5_1")
        - title: str (best-guess title from surrounding text)
        - page: int (1-indexed page number)
        - headers: list[str]
        - rows: list[list] (each row is a mix of str and float values)
        - section: str ("revenue" | "expenditure" | "capital" | "unknown")
    """
    tables_out: list[dict[str, Any]] = []
    current_section = "unknown"

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_num = page_idx + 1

            # Try to detect section from page text
            page_text = (page.extract_text() or "").lower()
            for marker in REVENUE_MARKERS:
                if marker in page_text:
                    current_section = "revenue"
                    break
            for marker in EXPENDITURE_MARKERS:
                if marker in page_text:
                    current_section = "expenditure"
                    break
            if "capital" in page_text and ("expenditure" in page_text or "account" in page_text):
                current_section = "capital"

            # Extract tables from this page
            raw_tables = page.extract_tables() or []

            for t_idx, raw_table in enumerate(raw_tables):
                if not raw_table or len(raw_table) < 2:
                    continue

                # Find the header row (first row that looks like a header)
                header_idx = None
                for r_idx, row in enumerate(raw_table):
                    if is_budget_table_header(row):
                        header_idx = r_idx
                        break

                if header_idx is None:
                    # If no explicit header found, check if data rows exist
                    data_rows = [r for r in raw_table if is_data_row(r)]
                    if len(data_rows) < 1:
                        continue
                    # Use first row as header
                    header_idx = 0

                header_row = raw_table[header_idx]
                headers = [str(cell or "").strip() for cell in header_row]

                # Collect data rows after the header
                data_rows: list[list[Any]] = []
                for row in raw_table[header_idx + 1 :]:
                    if not any(cell for cell in row):
                        continue  # skip blank rows

                    parsed_row: list[Any] = []
                    for j, cell in enumerate(row):
                        num = parse_indian_number(cell)
                        if num is not None and j > 0:
                            # Columns after the first are likely numeric
                            parsed_row.append(num)
                        else:
                            parsed_row.append(str(cell or "").strip())
                    data_rows.append(parsed_row)

                if not data_rows:
                    continue

                # Try to derive a title from text above the table on the page
                title = _guess_table_title(page_text, current_section, page_num, t_idx)

                tables_out.append({
                    "id": f"table_p{page_num}_{t_idx + 1}",
                    "title": title,
                    "page": page_num,
                    "headers": headers,
                    "rows": data_rows,
                    "section": current_section,
                })

    return tables_out


def _guess_table_title(
    page_text: str, section: str, page_num: int, table_idx: int
) -> str:
    """Heuristic: try to find a title-like line near the top of the page text."""
    lines = page_text.strip().split("\n")
    for line in lines[:5]:
        line = line.strip()
        # Title-like: not too long, contains letters, not a page number
        if 5 < len(line) < 100 and any(c.isalpha() for c in line):
            # Skip lines that look like just numbers or codes
            if not re.match(r"^[\d\s,.\-/]+$", line):
                return line.title()

    # Fallback
    return f"{section.title()} Account — Page {page_num}, Table {table_idx + 1}"


# ---------------------------------------------------------------------------
# Aggregation into by_function structure
# ---------------------------------------------------------------------------

def aggregate_by_function(
    tables: list[dict[str, Any]],
    function_mapping: dict[str, list[str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Aggregate table rows into per-function budget amounts.

    Scans every row in every table, tries to match the first column (budget
    head description) to a function, and accumulates the BE (budget estimate)
    amount from the last numeric column.

    Returns:
        (by_function dict, diagnostics dict with matched/unmatched info)
    """
    # Accumulate totals per function
    func_totals: dict[str, float] = {}
    func_prev_totals: dict[str, float] = {}  # for YoY growth (RE column)
    matched_heads: list[str] = []
    unmatched_heads: list[str] = []

    for table in tables:
        col_map = classify_columns(table["headers"])
        head_col = col_map.get("head", 0)

        # Figure out which column is BE (budget estimate) and RE (revised estimate)
        # Prefer classified columns; otherwise use last and second-to-last numeric cols
        be_col = col_map.get("be")
        re_col = col_map.get("re")

        if be_col is None:
            # Use the last column index as a guess for BE
            be_col = len(table["headers"]) - 1
        if re_col is None and be_col is not None and be_col > 1:
            re_col = be_col - 1

        for row in table["rows"]:
            if len(row) <= head_col:
                continue

            head_text = str(row[head_col]).strip() if row[head_col] else ""
            if not head_text:
                continue

            func_id = match_function(head_text, function_mapping)

            # Extract BE amount
            be_val = None
            if be_col is not None and be_col < len(row):
                val = row[be_col]
                if isinstance(val, (int, float)):
                    be_val = float(val)

            # Extract RE amount for YoY
            re_val = None
            if re_col is not None and re_col < len(row):
                val = row[re_col]
                if isinstance(val, (int, float)):
                    re_val = float(val)

            if func_id and be_val is not None:
                func_totals[func_id] = func_totals.get(func_id, 0) + be_val
                matched_heads.append(head_text)
                if re_val is not None:
                    func_prev_totals[func_id] = func_prev_totals.get(func_id, 0) + re_val
            else:
                if head_text and be_val is not None:
                    unmatched_heads.append(head_text)

    # Build the by_function output
    total_expenditure = sum(func_totals.values()) or 1  # avoid division by zero
    by_function: dict[str, dict[str, Any]] = {}

    for func_id, amount in sorted(func_totals.items()):
        share = round(amount / total_expenditure, 3)
        prev = func_prev_totals.get(func_id, 0)
        yoy = round((amount - prev) / prev, 2) if prev else 0.0
        by_function[func_id] = {
            "amount": round(amount, 2),
            "share": share,
            "yoy_growth": yoy,
        }

    diagnostics = {
        "matched": matched_heads,
        "unmatched": unmatched_heads,
    }

    return by_function, diagnostics


def compute_summary(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a revenue/expenditure summary from extracted tables.

    Looks for tables in the 'revenue' and 'expenditure' sections and sums
    the last numeric column of each.  Falls back to zero if sections are
    not clearly identified.
    """
    total_revenue = 0.0
    total_expenditure = 0.0

    for table in tables:
        section = table.get("section", "unknown")
        for row in table["rows"]:
            # Get the last numeric value in the row
            last_num = None
            for cell in reversed(row):
                if isinstance(cell, (int, float)):
                    last_num = float(cell)
                    break
            if last_num is None:
                continue

            if section == "revenue":
                total_revenue += last_num
            elif section in ("expenditure", "capital"):
                total_expenditure += last_num

    return {
        "total_revenue": round(total_revenue, 2),
        "total_expenditure": round(total_expenditure, 2),
        "unit": "lakhs",
    }


# ---------------------------------------------------------------------------
# Corporation ID inference from filename
# ---------------------------------------------------------------------------

def infer_corporation(filename: str) -> str | None:
    """Try to infer the corporation ID from a PDF filename.

    Matches patterns like: central.pdf, bengaluru_south_2026-27.pdf,
    BBMP_East_Budget.pdf, etc.

    Returns:
        Corporation ID string or None.
    """
    for pattern in CORP_FILENAME_PATTERNS:
        m = pattern.search(filename)
        if m:
            return m.group(1).lower()
    return None


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_single_pdf(
    pdf_path: Path,
    corporation_id: str,
    fiscal_year: str,
    function_mapping: dict[str, list[str]],
) -> dict[str, Any]:
    """Process a single budget PDF and return the structured output dict."""
    print(f"  Processing: {pdf_path.name} (corporation={corporation_id})")

    tables = extract_tables_from_pdf(pdf_path)
    print(f"    Tables found: {len(tables)}")
    print(
        f"    Total rows: {sum(len(t['rows']) for t in tables)}"
    )

    by_function, diagnostics = aggregate_by_function(tables, function_mapping)
    summary = compute_summary(tables)

    print(f"    Functions matched: {len(by_function)}")
    print(f"    Rows matched to functions: {len(diagnostics['matched'])}")
    print(f"    Rows unmatched: {len(diagnostics['unmatched'])}")

    if diagnostics["unmatched"]:
        # Show a few unmatched heads for debugging
        sample = diagnostics["unmatched"][:5]
        print(f"    Sample unmatched heads: {sample}")

    # Build serializable table list (strip diagnostics-only fields)
    tables_out = []
    for t in tables:
        tables_out.append({
            "id": t["id"],
            "title": t["title"],
            "page": t["page"],
            "headers": t["headers"],
            "rows": t["rows"],
        })

    return {
        "corporation_id": corporation_id,
        "fiscal_year": fiscal_year,
        "summary": summary,
        "by_function": by_function,
        "tables": tables_out,
    }


def write_output(data: dict[str, Any], output_dir: Path) -> Path:
    """Write the output JSON file and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{data['corporation_id']}.json"
    output_path = output_dir / filename

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"    Output written: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract budget data from Bengaluru corporation budget PDFs "
            "into structured JSON for the city governance portal."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/extract_budget.py -i data/raw/bengaluru/budgets/2026-27/\n"
            "  python3 scripts/extract_budget.py -i central.pdf -c central\n"
            "  python3 scripts/extract_budget.py -i input/ -y 2025-26 -o output/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Path to a PDF file or a directory containing budget PDFs.",
    )
    parser.add_argument(
        "-c", "--corporation",
        choices=sorted(VALID_CORPORATIONS),
        default=None,
        help=(
            "Corporation ID (central/south/east/west/north). "
            "Required when --input is a single file and the corporation "
            "cannot be inferred from the filename."
        ),
    )
    parser.add_argument(
        "-y", "--year",
        default=DEFAULT_YEAR,
        help=f"Fiscal year (default: {DEFAULT_YEAR}).",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--function-mapping",
        default=None,
        help=f"Path to function-mapping.json (default: {DEFAULT_FUNCTION_MAPPING}).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_OUTPUT_DIR
    mapping_path = (
        Path(args.function_mapping).resolve()
        if args.function_mapping
        else DEFAULT_FUNCTION_MAPPING
    )

    # Load function mapping
    if not mapping_path.is_file():
        print(
            f"WARNING: Function mapping not found at {mapping_path}\n"
            "Function-level aggregation will be skipped.",
            file=sys.stderr,
        )
        function_mapping: dict[str, list[str]] = {}
    else:
        function_mapping = load_function_mapping(mapping_path)
        print(f"Loaded function mapping: {len(function_mapping)} functions")

    # Determine list of PDFs to process
    pdf_files: list[tuple[Path, str]] = []  # (path, corporation_id)

    if input_path.is_file():
        # Single file mode
        if not input_path.suffix.lower() == ".pdf":
            print(f"ERROR: {input_path} is not a PDF file.", file=sys.stderr)
            sys.exit(1)

        corp_id = args.corporation or infer_corporation(input_path.name)
        if not corp_id:
            print(
                f"ERROR: Cannot infer corporation from filename '{input_path.name}'.\n"
                "Please provide --corporation explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)

        if corp_id not in VALID_CORPORATIONS:
            print(
                f"ERROR: '{corp_id}' is not a valid corporation. "
                f"Choose from: {', '.join(sorted(VALID_CORPORATIONS))}",
                file=sys.stderr,
            )
            sys.exit(1)

        pdf_files.append((input_path, corp_id))

    elif input_path.is_dir():
        # Directory mode — find all PDFs
        found = sorted(input_path.glob("*.pdf"))
        if not found:
            print(
                f"No PDF files found in {input_path}\n\n"
                "Budget PDFs for Bengaluru's 5 corporations (2026-27) can be\n"
                "downloaded from https://data.opencity.in once published.\n\n"
                "Expected files:\n"
                "  central.pdf (or bengaluru_central_2026-27.pdf)\n"
                "  south.pdf\n"
                "  east.pdf\n"
                "  west.pdf\n"
                "  north.pdf\n\n"
                f"Place them in: {input_path}",
            )
            sys.exit(0)

        for pdf_path in found:
            corp_id = infer_corporation(pdf_path.name)
            if corp_id and corp_id in VALID_CORPORATIONS:
                pdf_files.append((pdf_path, corp_id))
            else:
                print(
                    f"  SKIP: {pdf_path.name} — cannot infer corporation. "
                    "Filename must contain one of: "
                    f"{', '.join(sorted(VALID_CORPORATIONS))}"
                )

        if not pdf_files:
            print(
                "No PDFs with recognizable corporation names found.\n"
                "Expected filenames to contain one of: "
                f"{', '.join(sorted(VALID_CORPORATIONS))}",
                file=sys.stderr,
            )
            sys.exit(1)

    else:
        print(
            f"ERROR: {input_path} does not exist.\n\n"
            "Budget PDFs for Bengaluru's 5 corporations (2026-27) can be\n"
            "downloaded from https://data.opencity.in once published.\n\n"
            "Expected directory structure:\n"
            "  data/raw/bengaluru/budgets/2026-27/\n"
            "    central.pdf\n"
            "    south.pdf\n"
            "    east.pdf\n"
            "    west.pdf\n"
            "    north.pdf",
            file=sys.stderr,
        )
        sys.exit(1)

    # Process each PDF
    print(f"\nProcessing {len(pdf_files)} PDF(s) for fiscal year {args.year}...\n")
    results: list[Path] = []

    for pdf_path, corp_id in pdf_files:
        try:
            data = process_single_pdf(pdf_path, corp_id, args.year, function_mapping)
            out_path = write_output(data, output_dir)
            results.append(out_path)
        except Exception as e:
            print(f"  ERROR processing {pdf_path.name}: {e}", file=sys.stderr)
            continue

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  PDFs processed:  {len(results)} / {len(pdf_files)}")
    print(f"  Output directory: {output_dir}")
    for p in results:
        print(f"    - {p.name}")
    print()


if __name__ == "__main__":
    main()
