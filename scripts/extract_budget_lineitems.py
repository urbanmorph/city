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
    last numeric = total, -2 = current, -3 = pending, -4 = revised.

Function & sub-category headings appear as rows with no code and an English
heading. We track current_function / current_subcat as we walk the PDF.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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


def strip_kannada(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\(cid:\d+\)", "", s)
    s = re.sub(r"[\u0C80-\u0CFF]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
    return SUBCAT_CANONICAL.get(name, name)


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
    """Find a budget code in any cell of the row. Return (code, cell_index)."""
    for i, c in enumerate(cells):
        if not c:
            continue
        m = CODE_RE.search(str(c))
        if m:
            return m.group(0), i
    return None


def extract_numeric_cells(cells: list[str], start_idx: int) -> list[float]:
    """Return the numeric values found in cells[start_idx:]."""
    vals = []
    for c in cells[start_idx:]:
        if c is None or c == "":
            continue
        v = parse_num(str(c))
        if v is not None:
            vals.append(v)
        elif str(c).strip() in ("-", "–", "—", "_"):
            # Explicit null cell — count as None so we preserve column count
            vals.append(None)  # type: ignore
    # Filter to only floats for our "last N" semantics while preserving ORDER
    return vals


def extract_lineitems(pdf_path: Path) -> dict:
    functions_data: dict[str, dict] = {
        code: {"code": code, "name": name, "sub_categories": {}}
        for code, name in FUNCTIONS
    }

    current_function: str | None = None
    current_subcat: str | None = None
    in_payments = False

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if "– Payments" in text:
                in_payments = True
            # Receipt pages have "RECEIPTS" header and a "– Receipts" marker
            # with Kannada prefix; once we pass them we stay in payments mode.
            if not in_payments:
                # Heuristic: if we see "– Payments" token anywhere, we're there
                if "Payments" in text and "Receipts" not in text:
                    in_payments = True
            if not in_payments:
                continue

            tables = page.extract_tables() or []
            for raw in tables:
                if not raw:
                    continue
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
                    for c in cells[code_idx + 1 :]:
                        eng = english_from_cell(c)
                        if eng and parse_num(eng) is None and not CODE_RE.search(eng):
                            particulars = eng
                            break
                    # Some rows have particulars inside the same cell as the code —
                    # strip the code out and re-use.
                    if not particulars:
                        src = str(cells[code_idx])
                        eng = english_from_cell(CODE_RE.sub("", src))
                        if eng:
                            particulars = eng

                    # Collect numeric values after the code position
                    numeric_values: list[float] = []
                    for c in cells[code_idx + 1 :]:
                        if c is None or str(c).strip() == "":
                            continue
                        v = parse_num(str(c))
                        if v is not None:
                            numeric_values.append(v)

                    if len(numeric_values) < 1:
                        continue

                    # Last 3 values on payments side are: pending, current, total
                    # Revised 2025-26 is the 4th from the end.
                    total = numeric_values[-1]
                    current_val = numeric_values[-2] if len(numeric_values) >= 2 else None
                    pending = numeric_values[-3] if len(numeric_values) >= 3 else None
                    revised = numeric_values[-4] if len(numeric_values) >= 4 else None

                    # Sanity: pending+current ≈ total. If not, likely different layout.
                    if pending is not None and current_val is not None:
                        if abs((pending + current_val) - total) > max(2.0, total * 0.05):
                            # Layout may be: ... total (no pending/current split)
                            pending = None
                            current_val = None
                            # revised stays at -4 (still likely correct for 2024-25 actuals, interim, nov, revised, total)

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

    return {
        "corporation_id": "south",
        "fiscal_year": "2026-27",
        "unit": "lakhs",
        "total_2026_27": round(total_2026, 2),
        "total_revised_2025_26": round(total_revised, 2),
        "total_spillover": round(total_spill, 2),
        "yoy_pct": yoy_pct(total_2026, total_revised),
        "functions": out_functions,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    args = p.parse_args()

    pdf_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()
    if not pdf_path.is_file():
        print(f"ERROR: {pdf_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Reading: {pdf_path}")
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
