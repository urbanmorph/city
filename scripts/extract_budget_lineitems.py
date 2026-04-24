#!/usr/bin/env python3
"""
extract_budget_lineitems.py — Extract per-line-item budget detail from a BSCC budget PDF.

Approach: use pdfplumber's table extraction but locate columns dynamically per row.
Each data row has:
  - A budget code (NN-NNNNNN or NN-NNNNNN-NN) somewhere in the row
  - A particulars cell with Kannada + English
  - Numeric cells for the 7 value columns:
      Accounts 2024-25, Interim BE 2025-26, Accounts Nov 2025,
      Revised BE 2025-26, Pending & Spillover, Current Works, Total BE 2026-27
  - Because column count varies between pages/tables, we parse by position:
    last numeric = total, -2 = current, -3 = pending, -4 = revised,
    -5 = accounts_nov_2025, -6 = interim_be_2025_26, -7 = accounts_2024_25.

Function & sub-category headings appear as rows with no code and an English
heading. We track current_function / current_subcat as we walk the PDF.

The top-level JSON also captures the book's own summary tables (pages 3-4):
  - summary_receipts      (opening balance, own revenue, state/central/capital grants)
  - summary_expenditure   (establishment, operations/maintenance, capital, revenue transfers)
  - surplus_or_deficit
  - closing_balance
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF — used for cover/footer scraping
import pdfplumber


FUNCTIONS: list[tuple[str, str]] = [
    ("01", "Council"),
    ("02", "General Administration"),
    ("03", "Revenue"),
    ("04", "Town Planning and Regulation"),
    ("05", "Public Works"),
    ("06", "Solid Waste Management"),
    ("07", "Public Health-General"),
    ("08", "Public Health-Medical"),
    ("09", "Horticulture"),
    ("10", "Urban Forestry"),
    ("11", "Public Education"),
    ("12", "Social Welfare"),
]
FUNCTION_NAMES = {c: n for c, n in FUNCTIONS}

CODE_RE = re.compile(r"\b(\d{2})-(\d{4,6})(?:-(\d{1,2}))?\b")

# Sub-category canonical form (normalise typos/variants)
SUBCAT_CANONICAL: dict[str, str] = {
    "Capital Expnese": "Capital Expenses",
    "Capital Expneses": "Capital Expenses",
    "Operation and  Maintenances": "Operation and Maintenance",
    "Operation and Maintenances": "Operation and Maintenance",
    "Repaiirs and Maintenance of Road, Footpaths, Surface Drains, Flyovers, Bridges and Subways and Storm Water Drains": "Repairs & Maintenance — Roads, Footpaths, Flyovers, Subways, SWDs",
    "Repairs and Maintenance of Road, Footpaths, Surface Drains, Flyovers, Bridges and Subways and Storm Water Drains": "Repairs & Maintenance — Roads, Footpaths, Flyovers, Subways, SWDs",
    "Repairs and Maintenance of Street Light and Electrical": "Repairs & Maintenance — Street Light & Electrical",
    "Repairs and Maintenance of Water supply and UGD, Play Grounds, Dhobi Ghats,Lakes and Others": "Repairs & Maintenance — Water Supply, UGD, Playgrounds, Lakes",
    "Repairs and Maintenance of Buildings": "Repairs & Maintenance — Buildings",
    "SWM Operation and  Maintenances": "SWM Operation and Maintenance",
    "SWM Operation and Maintenances": "SWM Operation and Maintenance",
    "Ceremonies and Functions": "Functions and Ceremonies",
    "OBC/BCM & Minority Welfare Programmes": "OBC/BCM & Minority Welfare",
    "Repaiirs and Maintenance of Road": "Repairs & Maintenance — Roads, Footpaths, Flyovers, Subways, SWDs",
}


KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")


def strip_kannada(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\(cid:\d+\)", "", s)
    s = re.sub(r"[\u0C80-\u0CFF]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_kannada(s: str) -> str | None:
    """Return the Kannada-script portion of a cell, if any.

    The BSCC budget book renders Kannada via font-remapped CID glyphs, so
    the extracted text is a mix of directly-mapped Kannada characters and
    `(cid:NNNN)` tokens for the unmapped glyphs. We preserve whatever
    Kannada Unicode codepoints pdfplumber recovered (joined onto a single
    line, inner whitespace collapsed, CID tokens dropped, English words
    stripped) so reviewers can at least read a partial transliteration. If
    the recovery yields fewer than 3 Kannada characters, return None.
    """
    if not s:
        return None
    kan_parts: list[str] = []
    for line in s.split("\n"):
        # Strip CID tokens so we don't leak them into the output
        cleaned = re.sub(r"\(cid:\d+\)", "", line)
        # If the line contains any Kannada codepoint, keep only the
        # Kannada-script tokens (drop trailing English words that may be
        # part of a multi-line cell where Kannada and English mix on
        # the same line).
        if KANNADA_RE.search(cleaned):
            tokens = []
            for tok in cleaned.split():
                # Keep token only if it has a Kannada character; stop
                # collecting once we hit pure-English tokens (which
                # mark the start of the English particulars).
                if KANNADA_RE.search(tok):
                    tokens.append(tok)
                elif tokens:
                    # We've started collecting and now hit non-Kannada
                    # — likely the English half of a mixed line.
                    break
            if tokens:
                kan_parts.append(" ".join(tokens))
    if not kan_parts:
        return None
    joined = " ".join(kan_parts)
    joined = re.sub(r"\s+", " ", joined).strip()
    kan_chars = sum(1 for c in joined if "\u0C80" <= c <= "\u0CFF")
    if kan_chars < 3:
        return None
    return joined


def english_from_cell(cell: str | None) -> str:
    if not cell:
        return ""
    parts = []
    for line in cell.split("\n"):
        cleaned = strip_kannada(line)
        if cleaned:
            parts.append(cleaned)
    return re.sub(r"\s+", " ", " ".join(parts)).strip(" -_.|")


def parse_num(text: str | None) -> float | None:
    if text is None:
        return None
    t = str(text).strip()
    if not t or t in ("-", "–", "—", "_"):
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1]
    t = t.replace(",", "").replace("₹", "").replace(" ", "").strip()
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def canonical_subcat(name: str) -> str:
    # Strip stray leading punctuation (commas, dashes, spaces) that PDF extraction
    # sometimes leaves behind when the first tokens of a heading were Kannada-only.
    cleaned = re.sub(r"^[,\-\s]+", "", name).strip()
    return SUBCAT_CANONICAL.get(cleaned, cleaned)


def is_function_heading(english: str) -> str | None:
    """Return fcode if the english is essentially a function name by itself."""
    if not english:
        return None
    low = re.sub(r"[^a-z ]+", " ", english.lower())
    low = re.sub(r"\s+", " ", low).strip()
    for code, name in FUNCTIONS:
        canon = re.sub(r"[^a-z ]+", " ", name.lower())
        canon = re.sub(r"\s+", " ", canon).strip()
        if low == canon:
            return code
    # Also match variants
    variants = {
        "04": ["town planning regulation"],
        "07": ["public health general", "public health  general"],
        "08": ["public health medical", "public health  medical"],
        "10": ["urban forest", "urban forestry"],
    }
    for code, names in variants.items():
        if low in names:
            return code
    return None


SUBCAT_BLOCKLIST = [
    "payments",
    "receipts",
    "pending",
    "pending &",
    "pending & spilover",
    "spilover works",
    "current works",
    "particulars",
    "budget code",
    "budget estimate",
    "revised budget estimate",
    "interim budget estimate",
    "accounts upto november",
    "rs. in lakhs",
    "rs.in lakhs",
    "in lakhs",
    "total budget estimate",
    "march-2026",
    "sept-02 to march",
]

SUBCAT_ALLOWLIST = [
    "Establishment Expenses",
    "General Administrative Expenses",
    "Council and Corporators Expenses",
    "Functions and Ceremonies",
    "Ceremonies and Functions",
    "Repairs and Maintenance of Buildings",
    "Repaiirs and Maintenance of Road",
    "Repairs and Maintenance of Road",
    "Repairs and Maintenance of Street Light and Electrical",
    "Repairs and Maintenance of Water supply",
    "Capital Expenses",
    "Capital Expnese",
    "Capital Expneses",
    "Operation and Maintenance",
    "Operation and Maintenances",
    "Operation and  Maintenances",
    "Programme Expenses",
    "Health and Sanitation",
    "SWM Operation",
    "Pourakarmika Welfare",
    "SC/ST Welfare",
    "OBC/BCM",
    "Minority Welfare",
    "Extra-Ordinary",
    "Extraordinary",
    "Tax & Cess",
    "Non Tax Revenue",
    "GOK-Revenue Grant",
    "Other Grants",
    "Specific Purpose Grant",
    "Repairs and Maintenance",
    "Tree Canopy",
    "Training",
]


def looks_like_subcat_heading(english: str) -> bool:
    if not english or len(english) < 3:
        return False
    if re.search(r"\d", english):
        return False
    words = english.split()
    if len(words) < 2 or len(words) > 25:
        return False
    low = english.lower().strip()
    if low.startswith("total"):
        return False
    # Hard blocklist — anything in the header region
    for b in SUBCAT_BLOCKLIST:
        if b in low:
            return False
    # Must match an allowlist term (or contain one) to be accepted
    for a in SUBCAT_ALLOWLIST:
        if a.lower() in low:
            return True
    return False


def yoy_pct(curr: float, prev: float) -> float | None:
    if not prev:
        return None
    return round(((curr - prev) / prev) * 100, 1)


def row_has_code(cells: list[str]) -> tuple[str, int] | None:
    """Find a budget code in any cell of the row. Return (code, cell_index).

    Some cells split the code across lines (e.g. ``03-\\n200101``). We collapse
    internal whitespace around the hyphen before matching so those still match.
    """
    for i, c in enumerate(cells):
        if not c:
            continue
        s = str(c)
        # Normalise: join split codes like "03-\n200101", "03- 200101",
        # and the sub-suffix "05-330503-\n01" => "05-330503-01".
        normalised = re.sub(r"(\b\d{2}-)\s+(\d{4,6})", r"\1\2", s)
        normalised = re.sub(r"(\b\d{2}-\d{4,6}-)\s+(\d{1,2}\b)", r"\1\2", normalised)
        m = CODE_RE.search(normalised)
        if m:
            return m.group(0), i
    return None


# Header keyword patterns used to identify column positions per table.
HEADER_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("accounts_2024_25", re.compile(r"2024-25", re.IGNORECASE)),
    ("interim_be_2025_26", re.compile(r"Interim", re.IGNORECASE)),
    ("accounts_nov_2025", re.compile(r"November\s*2025|Nov\s*2025", re.IGNORECASE)),
    ("revised_2025_26", re.compile(r"Revised", re.IGNORECASE)),
    ("pending", re.compile(r"Pending", re.IGNORECASE)),
    ("current", re.compile(r"Current\s*Works", re.IGNORECASE)),
    ("total_2026_27", re.compile(r"Total", re.IGNORECASE)),
]


def detect_column_map(table: list[list[str | None]]) -> dict[str, int] | None:
    """Identify the 7 numeric data columns in a payments-data table.

    Strategy: scan the data rows (rows with a budget code) and count how
    many times each column index holds a numeric value (or an explicit
    null marker `_`/`-`). The 7 columns that appear most often ARE the
    data columns — accounts_2024_25, interim_be, nov, revised, pending,
    current, total, in left-to-right order.

    Returns a dict mapping field name -> column index, or None if fewer
    than 4 data columns could be identified (caller falls back to the
    legacy positional heuristic).
    """
    if not table:
        return None
    col_count: dict[int, int] = {}
    for row in table:
        if not row:
            continue
        cells = [(c if c is not None else "") for c in row]
        if not row_has_code(cells):
            continue
        for cidx, c in enumerate(cells):
            if c is None:
                continue
            s = str(c).strip()
            if s == "":
                continue
            # Treat dash markers as numeric-null (counted)
            if s in ("-", "–", "—", "_"):
                col_count[cidx] = col_count.get(cidx, 0) + 1
                continue
            if parse_num(s) is not None:
                col_count[cidx] = col_count.get(cidx, 0) + 1

    if not col_count:
        return None

    # Pick the 7 most-frequent columns
    sorted_cols = sorted(col_count.keys(), key=lambda c: (-col_count[c], c))
    top_cols = sorted(sorted_cols[:7])
    if len(top_cols) < 4:
        return None

    # Column layout: always ordered as
    #   [acc24, interim, nov, revised, pending, current, total]
    # When fewer than 7 columns are present, figure out WHICH column is
    # missing:
    #   - 7 cols: all present — straight mapping.
    #   - 6 cols: the 'pending' column is most often absent (many rows
    #             have the pending+current merged into a single current
    #             column). Assign the left 4 to acc24..revised, and the
    #             right 2 to current+total.
    #   - 5 cols: pending + one of the left-receipts columns absent.
    #             Leftmost 3 = acc24+interim+revised OR interim+nov+
    #             revised depending on page; right 2 = current+total.
    #             We conservatively assign left→acc24..revised, omitting
    #             'nov' (most commonly '_' on South pages).
    #   - 4 cols: too ambiguous — only assign right-side 2 (current,
    #             total) plus revised.
    col_map: dict[str, int] = {}
    if len(top_cols) >= 7:
        fields = [
            "accounts_2024_25",
            "interim_be_2025_26",
            "accounts_nov_2025",
            "revised_2025_26",
            "pending",
            "current",
            "total_2026_27",
        ]
        for i, field in enumerate(fields):
            col_map[field] = top_cols[i]
    elif len(top_cols) == 6:
        # One column is absent. Decide WHICH by inspecting the gap pattern
        # between consecutive columns. The book always packs the receipts
        # columns (acc24, interim, nov, revised) close together (gaps 1-2)
        # and then has a larger gap before the payments columns (pending,
        # current, total). The big gaps usually:
        #   - around index 3 (before 'current' if pending absent), OR
        #   - around index 1-2 (between interim/nov/revised when one of
        #     the receipts columns is absent)
        gaps = [top_cols[i + 1] - top_cols[i] for i in range(5)]
        max_gap_idx = max(range(5), key=lambda i: gaps[i])
        if max_gap_idx >= 3:
            # 'pending' column absent: [acc24, interim, nov, revised, current, total]
            field_order = [
                "accounts_2024_25",
                "interim_be_2025_26",
                "accounts_nov_2025",
                "revised_2025_26",
                "current",
                "total_2026_27",
            ]
        elif max_gap_idx == 0:
            # gap is between col0 and col1 — col0 is acc24, but the rest
            # are tightly packed; assume 'nov' absent and pending present
            field_order = [
                "accounts_2024_25",
                "interim_be_2025_26",
                "revised_2025_26",
                "pending",
                "current",
                "total_2026_27",
            ]
        else:
            # gap in the middle (index 1 or 2) — 'nov' column absent;
            # data columns are [acc24, interim, revised, pending, current, total]
            field_order = [
                "accounts_2024_25",
                "interim_be_2025_26",
                "revised_2025_26",
                "pending",
                "current",
                "total_2026_27",
            ]
        for i, field in enumerate(field_order):
            col_map[field] = top_cols[i]
    elif len(top_cols) == 5:
        # pending + one receipts-column absent (typically 'nov' since
        # 'Accounts upto November 2025' is commonly '_' on South pages)
        col_map["accounts_2024_25"] = top_cols[0]
        col_map["interim_be_2025_26"] = top_cols[1]
        col_map["revised_2025_26"] = top_cols[2]
        col_map["current"] = top_cols[3]
        col_map["total_2026_27"] = top_cols[4]
    else:
        # Only 4 — assume revised, pending, current, total (legacy shape)
        col_map["revised_2025_26"] = top_cols[0]
        col_map["pending"] = top_cols[1]
        col_map["current"] = top_cols[2]
        col_map["total_2026_27"] = top_cols[3]
    return col_map


def _cell_value(row: list[str | None], col_idx: int) -> str:
    """Return the cell text at col_idx, or ''."""
    if col_idx < 0 or col_idx >= len(row):
        return ""
    c = row[col_idx]
    if c is None:
        return ""
    return str(c).strip()


def _nearest_data_cell(row: list[str | None], col_idx: int, tolerance: int = 2) -> str:
    """Return the nearest non-empty cell value within ``tolerance`` columns
    either side of ``col_idx``.

    Budget tables use merged cells for the headers — ``pdfplumber`` assigns a
    single header cell to the leftmost column of its span, so the data cells
    below may land 1-2 columns to the left of the header col. This helper
    looks within a small window to pick the data value.
    """
    if col_idx < 0:
        return ""
    n = len(row)
    # Walk outward: col_idx, col_idx-1, col_idx+1, col_idx-2, col_idx+2 ...
    for delta in range(tolerance + 1):
        for sign in (0, -1, 1) if delta == 0 else (-1, 1):
            j = col_idx + sign * delta
            if 0 <= j < n:
                c = row[j]
                if c is not None and str(c).strip() != "":
                    return str(c).strip()
    return ""


# ---------------------------------------------------------------------------
# Top-level metadata extraction (summary tables, document date)
# ---------------------------------------------------------------------------

def _parse_summary_number(text: str) -> float | None:
    """Parse a number from a summary cell, tolerating trailing whitespace/null markers."""
    if text is None:
        return None
    t = str(text).strip().replace(",", "")
    if not t or t in ("-", "–", "—", "_"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _collect_first_pages_text(pdf_path: Path, n_pages: int = 4) -> list[str]:
    """Return plain text of the first N pages using PyMuPDF (survives font remap)."""
    doc = fitz.open(pdf_path)
    texts = []
    for i in range(min(n_pages, doc.page_count)):
        texts.append(doc[i].get_text() or "")
    doc.close()
    return texts


def extract_document_date(pdf_path: Path) -> str | None:
    """Best-effort document-date extraction from cover/footer of the PDF.

    BSCC budget books don't always carry a printed date; when they do, it is
    usually on the cover ('DATE: dd-mm-yyyy'). If we can't find one, return None.
    """
    doc = fitz.open(pdf_path)
    texts: list[str] = []
    for i in range(min(3, doc.page_count)):
        texts.append(doc[i].get_text() or "")
    for i in range(max(0, doc.page_count - 2), doc.page_count):
        texts.append(doc[i].get_text() or "")
    doc.close()
    blob = "\n".join(texts)
    # dd-mm-yyyy or dd/mm/yyyy
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", blob)
    if m:
        d, mo, y = m.groups()
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    return None


def _lookup_pair_after(lines: list[str], label_pattern: str, max_gap: int = 6) -> list[float]:
    """Find the label line, then return the next up-to-2 numeric lines.

    Operates on PyMuPDF line-split text. Each pair of numbers
    is the (revised 2025-26, budget 2026-27) column pair.

    If the first matched label is not immediately followed by numeric lines
    (e.g. it's a TOC entry on page 2 rather than the actual Financial
    Position/Abstract row on page 3-4), keep scanning subsequent matches
    until numeric values are found.
    """
    pat = re.compile(label_pattern, re.IGNORECASE)
    for i, line in enumerate(lines):
        if not pat.search(line):
            continue
        # Walk forward up to max_gap lines; collect numeric-only lines.
        found: list[float] = []
        for j in range(i + 1, min(len(lines), i + max_gap + 1)):
            tok = lines[j].strip()
            if not tok:
                continue
            v = _parse_summary_number(tok)
            if v is not None:
                found.append(v)
                if len(found) >= 2:
                    break
            else:
                # A non-numeric, non-empty line after at least one value means
                # we've exited the pair block.
                if found:
                    break
                # If we hit a non-numeric line with no numbers yet, stop
                # (this label is likely a TOC entry); try the next match.
                break
        if found:
            return found
    return []


def extract_summaries(pdf_path: Path) -> dict:
    """Extract the budget book's own summary tables from the first ~6 pages.

    Pages 3 & 4 of each BSCC budget book carry two high-level tables:
      - Financial Position table (page 3): broken down into Revenue + Capital +
        Extraordinary accounts and closing balance.
      - Abstract (page 4): opening balance, receipts total, payments total,
        closing balance.

    Field mapping used for the top-level JSON:
      summary_receipts: {
        opening_balance          — 'Opening Cash & Bank Balances'
        own_revenue              — Revenue Receipts (B1)
        state_grants, central_grants — split lives in the Receipts abstract
                                       (pp 5-6); the book itself shows them
                                       aggregated as 'Capital Receipts'
        capital_grants           — Capital Receipts (C1)
        extraordinary_receipts   — Extra-Ordinary Receipts (D1)
        total                    — Receipts abstract (page 4 line B)
      }
      summary_expenditure: {
        revenue_payments         — Revenue Payments (B2)
        capital_works            — Capital Payments (C2)
        extraordinary_payments   — Extra-Ordinary Payments (D2)
        establishment,           — not separated in the book summaries
        operations_maintenance,
        revenue_transfers
        total                    — Payments abstract (page 4 line C)
      }
      surplus_or_deficit, closing_balance — from the abstract (page 4)

    Values we can't confidently extract are left null; the object is returned
    either way. (The spec requires additive fields, never drops.)
    """
    texts = _collect_first_pages_text(pdf_path, n_pages=6)
    # Work line-by-line on the concatenated page text — each row in the
    # Financial Position/Abstract has a label followed by its two numeric
    # values on consecutive lines, which makes line-lookup robust.
    all_lines: list[str] = []
    for t in texts:
        all_lines.extend(t.split("\n"))

    def second_or_none(lst: list[float]) -> float | None:
        return lst[1] if len(lst) >= 2 else None

    # Abstract (pg 4): Receipts row has the total receipts; Payments row has
    # total payments. These need specific label disambiguation:
    # 'Opening Balance' label is unique on page 4; on pg 3 it is 'Opening
    # Cash & Bank Balances'. 'Closing Balance' appears in two places on
    # page 3 (intermediate + final) and once on page 4 — _lookup_pair_after
    # grabs the first match, which on pg 3 is the Revenue/Capital/Ext split
    # totals; we want the ABSTRACT page 4 version, so prefer 'Closing
    # Balance(A+B-C)' style patterns.
    opening_vals = _lookup_pair_after(all_lines, r"^\s*Opening\s+Balance\s*$")
    # For total receipts — the page-4 abstract row has label just "Receipts"
    # (preceded by the Kannada ๭౷ೕಂൟಗำ); use an exact match.
    receipts_vals = _lookup_pair_after(all_lines, r"^\s*Receipts\s*$")
    payments_vals = _lookup_pair_after(all_lines, r"^\s*Payments\s*$")
    closing_vals = _lookup_pair_after(all_lines, r"^\s*Closing\s+Balance\(A\+B-C\)")
    if not closing_vals:
        # Fallback: the Financial Position also has 'Closing Balance (A+E)'
        closing_vals = _lookup_pair_after(all_lines, r"^\s*Closing\s+Balance\s*\(A\+E\)")

    # Financial Position (pg 3) breakdown — exact labels
    rev_receipts = _lookup_pair_after(all_lines, r"^\s*Revenue\s+Receipts\s*$")
    rev_payments = _lookup_pair_after(all_lines, r"^\s*Revenue\s+Payments\s*$")
    cap_receipts = _lookup_pair_after(all_lines, r"^\s*Capital\s+Receipts\s*$")
    cap_payments = _lookup_pair_after(all_lines, r"^\s*Capital\s+Payments\s*$")
    ext_receipts = _lookup_pair_after(all_lines, r"^\s*Extra[-\s]*Ordinary\s+Receipts\s*$")
    ext_payments = _lookup_pair_after(all_lines, r"^\s*Extra[-\s]*Ordinary\s+Payments\s*$")

    # Opening Cash & Bank Balances — pg3 financial position
    opening_cash = _lookup_pair_after(all_lines, r"^\s*Opening\s+Cash\s+&\s+Bank")

    # Surplus/deficit — the 'Total Cash Surplus/Deficit (B+C+D)' row on pg 3
    total_surplus = _lookup_pair_after(
        all_lines, r"^\s*Total\s+Cash\s+Surplus/Deficit"
    )
    surplus_or_deficit = second_or_none(total_surplus)

    summary_receipts = {
        "opening_balance": second_or_none(opening_cash) or second_or_none(opening_vals),
        "own_revenue": second_or_none(rev_receipts),
        "state_grants": None,
        "central_grants": None,
        "capital_grants": second_or_none(cap_receipts),
        "extraordinary_receipts": second_or_none(ext_receipts),
        "total": second_or_none(receipts_vals),
    }
    summary_expenditure = {
        "establishment": None,
        "operations_maintenance": None,
        "capital_works": second_or_none(cap_payments),
        "revenue_payments": second_or_none(rev_payments),
        "extraordinary_payments": second_or_none(ext_payments),
        "revenue_transfers": None,
        "total": second_or_none(payments_vals),
    }

    if surplus_or_deficit is None and summary_receipts["total"] and summary_expenditure["total"]:
        surplus_or_deficit = round(summary_receipts["total"] - summary_expenditure["total"], 2)

    closing_balance = second_or_none(closing_vals)

    return {
        "summary_receipts": summary_receipts,
        "summary_expenditure": summary_expenditure,
        "surplus_or_deficit": surplus_or_deficit,
        "closing_balance": closing_balance,
    }


# ---------------------------------------------------------------------------
# Main extraction walk
# ---------------------------------------------------------------------------

def extract_lineitems(pdf_path: Path) -> dict:
    functions_data: dict[str, dict] = {
        code: {"code": code, "name": name, "sub_categories": {}}
        for code, name in FUNCTIONS
    }

    current_function: str | None = None
    current_subcat: str | None = None
    # Per sub-category: first page number on which we saw the heading
    subcat_source_pages: dict[tuple[str, str], int] = {}

    with pdfplumber.open(pdf_path) as pdf:
        book_pages_total = len(pdf.pages)
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            # Each page is classified per-page (not a sticky flag), by looking at
            # the top ~300 chars (the page header band). This is robust to the
            # case variations across corps — South/Central/West headers say
            # "– Payments" (title case), North/East say "– PAYMENTS" (uppercase).
            # A page is a payments-data page iff its header mentions "payments"
            # but not "receipts". This correctly excludes:
            #   - Receipts data pages (header has "receipts")
            #   - The combined "Receipts & Payments" abstract page
            #   - Cover/TOC/Kannada-only pages (no English marker at all)
            header = text[:300].lower()
            if "payments" not in header or "receipts" in header:
                continue
            # Reset sub-category context when we hit a new page so subcategory
            # names don't leak across function boundaries the payments section.
            # current_function is set by the function heading logic below.

            tables = page.extract_tables() or []
            for raw in tables:
                if not raw:
                    continue
                # Identify column positions from the header rows.
                col_map = detect_column_map(raw)
                for row in raw:
                    if not row or not any(c for c in row):
                        continue
                    cells = [(c if c is not None else "") for c in row]

                    code_info = row_has_code(cells)

                    if code_info is None:
                        # Non-data row: try to detect heading in any cell
                        for c in cells:
                            english = english_from_cell(c)
                            if not english:
                                continue
                            fcode = is_function_heading(english)
                            if fcode:
                                current_function = fcode
                                current_subcat = None
                                break
                            if looks_like_subcat_heading(english):
                                if current_function is None:
                                    # Try to infer function from page position
                                    continue
                                sub = canonical_subcat(english)
                                current_subcat = sub
                                functions_data[current_function]["sub_categories"].setdefault(
                                    sub, {"name": sub, "items": []}
                                )
                                key = (current_function, sub)
                                # Remember the first page where we saw this sub-cat
                                subcat_source_pages.setdefault(key, page_idx + 1)
                                break
                        continue

                    code_str, code_idx = code_info
                    func_prefix = code_str.split("-")[0]
                    if func_prefix not in functions_data:
                        continue
                    current_function = func_prefix

                    # Particulars: look in the cell at code_idx+1 or in cells after
                    # code_idx. Pick the first cell with non-numeric english text.
                    particulars = ""
                    name_local_source = ""
                    for c in cells[code_idx + 1 :]:
                        eng = english_from_cell(c)
                        if eng and parse_num(eng) is None and not CODE_RE.search(eng):
                            particulars = eng
                            name_local_source = c or ""
                            break
                    # Some rows have particulars inside the same cell as the code —
                    # strip the code out and re-use.
                    if not particulars:
                        src = str(cells[code_idx])
                        eng = english_from_cell(CODE_RE.sub("", src))
                        if eng:
                            particulars = eng
                            name_local_source = src

                    name_local = extract_kannada(name_local_source) if name_local_source else None

                    # Collect real numeric values for old-style fields —
                    # this replicates the legacy extraction exactly so the
                    # shipped revised/pending/current/total numbers do not
                    # drift.
                    float_values: list[float] = []
                    for c in cells[code_idx + 1 :]:
                        if c is None:
                            continue
                        s = str(c).strip()
                        if s == "" or s in ("-", "–", "—", "_"):
                            continue
                        v = parse_num(s)
                        if v is not None:
                            float_values.append(v)

                    if not float_values:
                        continue

                    # Old shipped semantics: treat the RIGHTMOST four real
                    # numbers as (revised, pending, current, total). Do NOT
                    # change this — existing downstream data depends on it.
                    total = float_values[-1]
                    current_val = float_values[-2] if len(float_values) >= 2 else None
                    pending = float_values[-3] if len(float_values) >= 3 else None
                    revised = float_values[-4] if len(float_values) >= 4 else None

                    # Sanity: pending+current ≈ total. If not, likely
                    # different layout — don't split.
                    if pending is not None and current_val is not None:
                        if abs((pending + current_val) - total) > max(2.0, total * 0.05):
                            pending = None
                            current_val = None

                    # New-field extraction: pick data cells at the column
                    # indices detected by detect_column_map(). The
                    # detection uses the most-frequent data columns of the
                    # table, which corresponds correctly to the BSCC
                    # columns even when pdfplumber reports them at indices
                    # slightly different from the header-band positions.
                    accounts_2024_25: float | None = None
                    interim_be_2025_26: float | None = None
                    accounts_nov_2025: float | None = None
                    if col_map:
                        def pick_col(field: str) -> float | None:
                            ci = col_map.get(field, -1)
                            if ci < 0 or ci >= len(cells):
                                return None
                            c = cells[ci]
                            if c is None:
                                return None
                            s = str(c).strip()
                            if s == "" or s in ("-", "–", "—", "_"):
                                return None
                            return parse_num(s)

                        accounts_2024_25 = pick_col("accounts_2024_25")
                        interim_be_2025_26 = pick_col("interim_be_2025_26")
                        accounts_nov_2025 = pick_col("accounts_nov_2025")
                    else:
                        # Fallback: positional (preserves '-' as None)
                        positional_values: list[float | None] = []
                        for c in cells[code_idx + 1 :]:
                            if c is None:
                                continue
                            s = str(c).strip()
                            if s == "":
                                continue
                            if s in ("-", "–", "—", "_"):
                                positional_values.append(None)
                                continue
                            v = parse_num(s)
                            if v is not None:
                                positional_values.append(v)
                        while positional_values and positional_values[-1] is None:
                            positional_values.pop()

                        def pos_at(n: int) -> float | None:
                            return positional_values[-n] if len(positional_values) >= n else None

                        accounts_nov_2025 = pos_at(5)
                        interim_be_2025_26 = pos_at(6)
                        accounts_2024_25 = pos_at(7)

                    if current_subcat is None:
                        current_subcat = "Other"
                        functions_data[current_function]["sub_categories"].setdefault(
                            current_subcat, {"name": current_subcat, "items": []}
                        )
                    functions_data[current_function]["sub_categories"].setdefault(
                        current_subcat, {"name": current_subcat, "items": []}
                    )

                    # Dedupe — same code shouldn't appear twice in same subcat (pdfplumber sometimes double-emits)
                    items = functions_data[current_function]["sub_categories"][current_subcat]["items"]
                    if any(it["code"] == code_str for it in items):
                        continue

                    items.append(
                        {
                            "code": code_str,
                            "name": particulars or "(unnamed)",
                            "name_local": name_local,
                            "accounts_2024_25": accounts_2024_25,
                            "interim_be_2025_26": interim_be_2025_26,
                            "accounts_nov_2025": accounts_nov_2025,
                            "revised_2025_26": revised,
                            "pending": pending,
                            "current": current_val,
                            "total_2026_27": total,
                        }
                    )

    # Roll up
    out_functions = []
    for code, fname in FUNCTIONS:
        fdata = functions_data[code]
        subs_out = []
        f_total = 0.0
        f_revised = 0.0
        f_spill = 0.0
        for sub_name, sub in fdata["sub_categories"].items():
            items = sub["items"]
            if not items:
                continue
            s_total = sum((it["total_2026_27"] or 0) for it in items)
            s_revised = sum((it["revised_2025_26"] or 0) for it in items)
            s_spill = sum((it["pending"] or 0) for it in items)
            for it in items:
                it["yoy_pct"] = yoy_pct(it.get("total_2026_27") or 0, it.get("revised_2025_26") or 0)
            subs_out.append(
                {
                    "name": sub_name,
                    "source_page": subcat_source_pages.get((code, sub_name)),
                    "total_2026_27": round(s_total, 2),
                    "revised_2025_26": round(s_revised, 2),
                    "spillover": round(s_spill, 2),
                    "yoy_pct": yoy_pct(s_total, s_revised),
                    "items": items,
                }
            )
            f_total += s_total
            f_revised += s_revised
            f_spill += s_spill
        subs_out.sort(key=lambda s: s["total_2026_27"], reverse=True)
        out_functions.append(
            {
                "code": code,
                "name": fname,
                "total_2026_27": round(f_total, 2),
                "revised_2025_26": round(f_revised, 2),
                "spillover": round(f_spill, 2),
                "yoy_pct": yoy_pct(f_total, f_revised),
                "sub_categories": subs_out,
            }
        )

    total_2026 = sum(f["total_2026_27"] for f in out_functions)
    total_revised = sum(f["revised_2025_26"] for f in out_functions)
    total_spill = sum(f["spillover"] for f in out_functions)

    # Top-level metadata
    source_file = pdf_path.name
    document_date = extract_document_date(pdf_path)
    summaries = extract_summaries(pdf_path)

    return {
        "corporation_id": CORP_ID,
        "fiscal_year": "2026-27",
        "unit": "lakhs",
        "source_file": source_file,
        "document_date": document_date,
        "book_pages_total": book_pages_total,
        "total_2026_27": round(total_2026, 2),
        "total_revised_2025_26": round(total_revised, 2),
        "total_spillover": round(total_spill, 2),
        "yoy_pct": yoy_pct(total_2026, total_revised),
        "summary_receipts": summaries["summary_receipts"],
        "summary_expenditure": summaries["summary_expenditure"],
        "surplus_or_deficit": summaries["surplus_or_deficit"],
        "closing_balance": summaries["closing_balance"],
        "functions": out_functions,
    }


CORP_ID = "south"  # overridden by --corp


def main() -> None:
    global CORP_ID
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--corp", default="south", help="corporation id (stamped into output JSON)")
    args = p.parse_args()

    CORP_ID = args.corp
    pdf_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()
    if not pdf_path.is_file():
        print(f"ERROR: {pdf_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {pdf_path} (corp={CORP_ID})")
    data = extract_lineitems(pdf_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Wrote: {out_path}")
    print(f"Total 2026-27:          ₹{data['total_2026_27']:>12,.0f} lakh")
    print(f"Total Revised 2025-26:  ₹{data['total_revised_2025_26']:>12,.0f} lakh")
    print(f"Total Spillover:        ₹{data['total_spillover']:>12,.0f} lakh")
    print()
    for f in data["functions"]:
        n_items = sum(len(s["items"]) for s in f["sub_categories"])
        print(
            f"  {f['code']} {f['name']:<32} ₹{f['total_2026_27']:>12,.0f}L  "
            f"({len(f['sub_categories'])} sub-cats, {n_items} items)"
        )


if __name__ == "__main__":
    main()
