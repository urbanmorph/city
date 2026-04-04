#!/usr/bin/env python3
"""
Split the consolidated "Public Works" head in GBA corporation budget JSONs
into separate functional categories: roads-bridges, street-lighting,
water-supply, sewerage-drainage, fire-services.

Reads the 5 corporation PDFs with pdfplumber, extracts EXPENDITURE line items
from the Public Works section (dept code 05-), classifies each by budget code
range and English keyword, then updates the existing JSON files.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RAW_DIR = Path(__file__).parent  # data/raw/bengaluru/budgets/2026-27
JSON_DIR = RAW_DIR.parents[3] / "bengaluru" / "budgets" / "2026-27"

CORPS = ["central", "south", "east", "west", "north"]

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

KEYWORD_RULES = [
    (r'\bfire\b', 'fire-services'),
    (r'street\s*light|LED\b|high\s*mast|BESCOM|\blighting\b'
     r'|electricity\s+charges\s+street|Electricity\s+Charges\s+Street'
     r'|crematori', 'street-lighting'),
    (r'water\s*supply|borewell|bore\s*well|drinking\s*water|water\s*tank'
     r'|BWSSB|piped\s*water', 'water-supply'),
    (r'\bdrain|sewerage|storm\s*water|\bSWD\b|rajakaluve|rajak[a ]luve'
     r'|\bnalah?\b|\bUGD\b|\bSTP\b|sewage|desilting', 'sewerage-drainage'),
    (r'\broad\b|\broads\b|bridge|flyover|footpath|asphalting|concreting'
     r'|tar\s*road|subway|white\s*topping|junction|pot\s*hole|pothole'
     r'|arterial|collector.*street|local.*street|resurfacing'
     r'|Sky\s*Walk|Sky\s*deck', 'roads-bridges'),
]


def classify_item(code: str, description: str) -> str:
    """Classify a Public Works line item into a functional category."""
    code_digits = re.sub(r'[^0-9]', '', code)

    if len(code_digits) >= 6:
        subgroup = code_digits[2:6]

        # ---- R&M sub-groups (05-22XXXX) ----
        if subgroup in ('2204', '2205'):
            return 'roads-bridges'
        if subgroup == '2206':
            # Roads R&M - code-based classification takes priority
            return 'roads-bridges'
        if subgroup == '2207':
            # 2207 can be bridges OR SWD - check keywords
            desc_lower = description.lower()
            if re.search(r'swd|storm\s*water\s*drain', desc_lower):
                return 'sewerage-drainage'
            return 'roads-bridges'
        if subgroup == '2208':
            return 'sewerage-drainage'
        if subgroup == '2209':
            return 'street-lighting'
        if subgroup == '2201':
            # Electricity charges - check if street lighting or office
            desc_lower = description.lower()
            if re.search(r'street\s*light|sfc.*grant|electrical\s*maintenance', desc_lower):
                return 'street-lighting'
            if re.search(r'office|bccc\s*office', desc_lower):
                return 'roads-bridges'  # admin overhead
            return 'street-lighting'  # default for electricity charges
        if subgroup == '2215':
            # 2215 = Electrical installations, mostly street-lighting
            # But 221599 = Other fixed assets (could be non-electrical)
            desc_lower = description.lower()
            if re.search(r'dhobi|dhoby|playground|play\s*ground|lake|park', desc_lower):
                return 'roads-bridges'
            return 'street-lighting'
        if subgroup == '2218':
            return 'sewerage-drainage'
        if subgroup == '2221':
            # Rain water harvesting and misc
            desc_lower = description.lower()
            if re.search(r'rain\s*water|calamit', desc_lower):
                return 'sewerage-drainage'
            return 'roads-bridges'

        # ---- Capital works (05-40XXXX) ----
        if subgroup in ('4001',):
            # Land acquisition / fencing - generally roads
            desc_lower = description.lower()
            # Only classify as drainage if specifically about lake development
            if re.search(r'development.*lake|improvement.*lake|construction.*lake', desc_lower):
                return 'sewerage-drainage'
            return 'roads-bridges'
        if subgroup in ('4002',):
            # Buildings capital
            return 'roads-bridges'
        if subgroup in ('4003',):
            # Mixed civil works - parks, grounds, crematoriums, borewells, UGD
            desc_lower = description.lower()
            if re.search(r'borewell|water\s*supply|ugd', desc_lower):
                return 'water-supply'
            if re.search(r'\bdrain|swd|storm\s*water|lake|stp', desc_lower):
                return 'sewerage-drainage'
            return 'roads-bridges'
        if subgroup in ('4004',):
            # 4004XX = roads capital works / general development
            # Most are road-related; only override for very specific keywords
            desc_lower = description.lower()
            if re.search(r'\bstreet\s*light\b', desc_lower):
                return 'street-lighting'
            if re.search(r'\bwater\s*supply\b|\bborewell\b', desc_lower):
                return 'water-supply'
            if re.search(r'\bfire\b', desc_lower):
                return 'fire-services'
            return 'roads-bridges'
        if subgroup == '4006':
            # 4006 = UGD/drains/water supply, but 400699 can be roads
            desc_lower = description.lower()
            if re.search(r'junction|footpath|sky\s*walk|road', desc_lower):
                return 'roads-bridges'
            return 'sewerage-drainage'
        if subgroup == '4007':
            return 'street-lighting'
        if subgroup == '4008':
            return 'water-supply'
        if subgroup == '4009':
            return 'fire-services'
        if subgroup == '4010':
            return 'roads-bridges'  # computers, equipment

    # ---- Keyword fallback ----
    desc_lower = description.lower()
    for pattern, func in KEYWORD_RULES:
        if re.search(pattern, desc_lower):
            return func

    # ---- Electrical sub-codes ----
    if len(code_digits) >= 6:
        subgroup = code_digits[2:6]
        if subgroup == '2215':
            return 'street-lighting'

    return 'roads-bridges'


def parse_indian_number(s: str) -> float:
    """Parse a number string, removing commas and underscores."""
    s = s.strip().replace(',', '').replace(' ', '').replace('_', '')
    if not s or s in ('-', '–', '.'):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_expenditure_code(code: str) -> bool:
    """Check if a 05-XXXXXX code is an expenditure code (not revenue)."""
    code_digits = re.sub(r'[^0-9]', '', code)
    if len(code_digits) < 4:
        return False
    prefix = code_digits[2:4]
    # Revenue codes start with 05-1X, expenditure with 05-2X, 05-3X, 05-4X
    return prefix[0] in ('2', '3', '4')


def _normalize_text(text: str) -> str:
    """Preprocess page text: clean CID codes, rejoin split budget codes."""
    text = re.sub(r'\(cid:\d+\)', '', text)

    # Join split budget codes. PDFs may split "05-200101" across lines as:
    #   "05-\n200101"  or  "05- _\n_ 200101"  or  "05-\n_\n200101"
    # Also handle sub-codes: "05-210913-\n01" -> "05-210913-01"

    # Simple cases: 05- immediately followed by digits on next line
    for _ in range(3):
        text = re.sub(r'(05-\d*-?)\s*_?\s*\n\s*_?\s*(\d{4,})', r'\1\2', text)
    text = re.sub(r'(05-)\s*\n\s*-\s*\n?\s*(\d{4,})', r'\1\2', text)

    # Complex case: codes split across multiple lines with text/numbers between.
    # e.g. East PDF:  "05- <Kannada text>"
    #                 "- 662.00 - 662.00 0.00 2500.00 2500.00"
    #                 "200101 Pay of Officers and Staff"
    # South PDF:      "05-"
    #                 "_ 6.00 6.00 0.00 25.00 25.00"
    #                 "221201 Repairs & ..."
    # Strategy: if a line starts with "05-" but doesn't have a complete code
    # (05-XXXXXX), look for a standalone 6-digit number at the start of one
    # of the next 4 lines and merge them.
    lines = text.split('\n')
    new_lines = []
    skip_next = set()
    for i, line in enumerate(lines):
        if i in skip_next:
            continue
        # Check if line starts with "05-" but does NOT contain a complete code
        if re.match(r'^05-', line) and not re.match(r'^05-\d{4,}', line):
            # Look in the next 4 lines for a 6-digit code at start
            found = False
            for j in range(i + 1, min(i + 5, len(lines))):
                code_m = re.match(r'^\s*_?\s*(\d{6}(?:-\d+)?)\b(.*)', lines[j])
                if code_m:
                    # Merge: "05-XXXXXX" + rest of that line
                    code_digits = code_m.group(1)
                    rest = code_m.group(2)
                    merged = f"05-{code_digits}{rest}"
                    new_lines.append(merged)
                    skip_next.add(j)
                    found = True
                    break
            if not found:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return '\n'.join(new_lines)


def _has_exp_05(text: str) -> bool:
    """Check if text has expenditure 05- budget codes."""
    return bool(re.search(r'05-(2[0-9]|3[2-5]|4[0-9])\d{2,}', text))


def extract_public_works(pdf_path: str, debug: bool = False) -> dict:
    """
    Extract and classify Public Works EXPENDITURE line items from a PDF.

    Returns dict with items, totals, pw_total_pdf.
    """
    pdf = pdfplumber.open(pdf_path)
    all_items = []
    pw_total_pdf = 0.0

    # Phase 1: Find PW expenditure pages.
    # Strategy: find the SECOND "05 - Public Works" header (first is receipts),
    # or the one on a Payments page. Then collect all pages with 05-2/3/4 codes
    # until "Total Public Works".

    # Scan all pages with normalised text
    page_info = []
    for pg_idx, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        text = _normalize_text(text)
        page_info.append({
            'idx': pg_idx,
            'text': text,
            'is_payments': bool(re.search(r'PAYMENTS|Payments', text)),
            'has_exp_05': _has_exp_05(text),
            'has_pw_header': bool(re.search(r'05\s*-\s*Public Works', text)),
            'has_total_pw': bool(re.search(r'Total\s+Public\s+Works', text, re.IGNORECASE)),
        })

    # Also detect receipts pages
    for p in page_info:
        p['is_receipts'] = bool(re.search(r'Receipts', p['text']))

    # Find all PW header pages
    pw_header_pages = [p for p in page_info if p['has_pw_header']]

    # Pick the expenditure PW start page:
    # MUST be on a Payments page (not Receipts)
    start_pg_idx = None
    for p in pw_header_pages:
        if p['is_payments'] and not p['is_receipts']:
            start_pg_idx = p['idx']
            break
    # Fallback: PW header page that has expenditure codes and is NOT receipts
    if start_pg_idx is None:
        for p in pw_header_pages:
            if p['has_exp_05'] and not p['is_receipts']:
                start_pg_idx = p['idx']
                break
    # Last resort: take the last PW header (most likely expenditure)
    if start_pg_idx is None and len(pw_header_pages) >= 2:
        start_pg_idx = pw_header_pages[-1]['idx']
    if start_pg_idx is None and len(pw_header_pages) >= 1:
        start_pg_idx = pw_header_pages[-1]['idx']

    if start_pg_idx is None:
        pdf.close()
        return {'items': [], 'totals': {}, 'pw_total_pdf': 0.0}

    # Collect pages from start until Total Public Works (skip receipts pages)
    pw_exp_pages = []
    for p in page_info:
        if p['idx'] < start_pg_idx:
            continue
        # Skip if this is a receipts page (not payments)
        if p['is_receipts'] and not p['is_payments']:
            continue
        if p['has_exp_05'] or p['has_total_pw'] or p['has_pw_header']:
            pw_exp_pages.append(p['idx'])
        if p['has_total_pw'] and p['idx'] > start_pg_idx:
            break

    # Include ALL intermediate pages between first and last detected PW pages.
    # Some pages may have split codes that aren't detected by _has_exp_05
    # on their own, but contain PW line items.
    if pw_exp_pages:
        first_pg = pw_exp_pages[0]
        last_pg = pw_exp_pages[-1]
        for pg_idx in range(first_pg, last_pg + 1):
            if pg_idx not in pw_exp_pages:
                p = page_info[pg_idx]
                # Only include if it's a Payments page (not just receipts)
                if p['is_payments'] or not p['is_receipts']:
                    pw_exp_pages.append(pg_idx)
        pw_exp_pages.sort()

    if debug:
        print(f"  PW expenditure pages: {[p+1 for p in pw_exp_pages]}")

    if not pw_exp_pages:
        pdf.close()
        return {'items': [], 'totals': {}, 'pw_total_pdf': 0.0}

    # Phase 2: Collect all normalised text from PW expenditure pages
    all_lines = []
    for pg_idx in pw_exp_pages:
        text = page_info[pg_idx]['text']
        lines = text.split('\n')
        for line in lines:
            all_lines.append(line)

    # Phase 2b: Fix cross-page split codes.
    # After joining all pages, some "05-" at end of one page's lines
    # correspond to code digits at start of the next page's lines.
    merged_lines = []
    i = 0
    while i < len(all_lines):
        line = all_lines[i]
        # If this line starts with "05-" but has no complete code,
        # and a subsequent line (within 4) starts with 6-digit code
        if re.match(r'^05-', line) and not re.match(r'^05-\d{4,}', line):
            found = False
            for j in range(i + 1, min(i + 5, len(all_lines))):
                code_m = re.match(r'^\s*_?\s*(\d{6}(?:-\d+)?)\b(.*)', all_lines[j])
                if code_m:
                    code_digits = code_m.group(1)
                    rest = code_m.group(2)
                    merged_lines.append(f"05-{code_digits}{rest}")
                    # Keep intermediate lines (they may have numbers)
                    for k in range(i + 1, j):
                        merged_lines.append(all_lines[k])
                    i = j + 1
                    found = True
                    break
            if not found:
                merged_lines.append(line)
                i += 1
        else:
            merged_lines.append(line)
            i += 1
    all_lines = merged_lines

    # Phase 3: Find Total Public Works
    for i, line in enumerate(all_lines):
        if re.search(r'Total\s+Public\s+Works', line, re.IGNORECASE):
            # Numbers might be on this line or the previous line
            nums = re.findall(r'[\d,]+\.\d{2}', line)
            if nums:
                pw_total_pdf = parse_indian_number(nums[-1])
            elif i > 0:
                nums = re.findall(r'[\d,]+\.\d{2}', all_lines[i - 1])
                if nums:
                    pw_total_pdf = parse_indian_number(nums[-1])
            break

    # Phase 4: Find all budget code entries with their numbers
    # Pattern: a line starting with 05-XXXXXX, possibly with text and numbers
    # Numbers may continue on the next line
    code_entries = []  # list of (line_index, code)

    for i, line in enumerate(all_lines):
        m = re.match(r'(05-\d{4,}(?:-\d+)?)', line)
        if m:
            code = m.group(1)
            if is_expenditure_code(code):
                code_entries.append((i, code))

    # Phase 5: For each code entry, find its numbers and description
    for idx, (line_i, code) in enumerate(code_entries):
        # Find the end boundary for this entry
        if idx + 1 < len(code_entries):
            end_i = code_entries[idx + 1][0]
        else:
            end_i = len(all_lines)

        # Don't go past Total Public Works
        for j in range(line_i + 1, end_i):
            if re.search(r'Total\s+Public\s+Works', all_lines[j], re.IGNORECASE):
                end_i = j
                break

        # Also stop at sub-total lines
        actual_end = end_i
        for j in range(line_i + 1, end_i):
            # Sub-total: "ಒ Total" at the start
            if re.match(r'ಒ\s+Total', all_lines[j]) and j > line_i:
                actual_end = j
                break

        # Collect text from line_i to actual_end
        block_lines = all_lines[line_i:actual_end]

        # Extract the budget row numbers.
        # Strategy: The budget row has 5-6 numbers (columns) laid out as:
        #   BE 2025-26 | Accounts Nov 2025 | RE 2025-26 | Pending | Current | Total
        # or for North PDF (5 cols):
        #   Accounts 2024-25 | BE 2025-26 | RE upto Nov 2025 | Pending+Current | Total
        #
        # Numbers appear on the code line itself, or on the very next line
        # if the code line only has Kannada text.
        # IMPORTANT: description continuation lines may contain embedded
        # numbers like "Rs.50.00 Lakhs" — we must NOT use those.

        # First, try to get numbers from the code line itself
        code_line = all_lines[line_i]
        code_line_nums = re.findall(r'[\d,]+\.\d{2}', code_line)

        # If the code line has >= 5 numbers, that's the budget row
        if len(code_line_nums) >= 5:
            budget_nums = code_line_nums
        elif len(code_line_nums) == 0:
            # No numbers on code line — check the next 1-2 lines for
            # a line that looks like a pure number row (5-6 decimals)
            budget_nums = []
            for offset in range(1, min(4, len(block_lines))):
                next_line = block_lines[offset] if offset < len(block_lines) else ''
                next_nums = re.findall(r'[\d,]+\.\d{2}', next_line)
                if len(next_nums) >= 5:
                    budget_nums = next_nums
                    break
                # Also accept a line that is mostly numbers (e.g. "154.00 0.00 0.00 25.00 400.00 425.00")
                stripped = next_line.strip()
                non_num = re.sub(r'[\d,.\s]+', '', stripped)
                if len(next_nums) >= 4 and len(non_num) < 10:
                    budget_nums = next_nums
                    break
        else:
            # Code line has some numbers but fewer than 5.
            # Check if the next line has more numbers that complete the row.
            budget_nums = code_line_nums
            if len(block_lines) > 1:
                next_nums = re.findall(r'[\d,]+\.\d{2}', block_lines[1])
                # If next line is mostly a number row, merge
                stripped = block_lines[1].strip()
                non_num = re.sub(r'[\d,.\s]+', '', stripped)
                if len(next_nums) >= 3 and len(non_num) < 15:
                    budget_nums = code_line_nums + next_nums

        # Extract English description from the block
        desc_parts = []
        for bl in block_lines:
            cleaned = re.sub(r'05-\d{4,}(?:-\d+)?', '', bl)
            cleaned = re.sub(r'\b\d{6}\b', '', cleaned)
            cleaned = re.sub(r'[\d,]+\.\d{2}', '', cleaned)
            cleaned = cleaned.strip()
            if re.search(r'[A-Za-z]{3,}', cleaned):
                if any(h in cleaned for h in ['Budget', 'Estimate', 'Particulars',
                                               'Accounts', 'Payments', 'Revised',
                                               'Spilover', 'Current', 'Pending']):
                    continue
                desc_parts.append(cleaned)

        description = ' '.join(desc_parts)
        description = re.sub(r'\s+', ' ', description).strip()

        # The last number is BE 2026-27 total
        if budget_nums:
            amount = parse_indian_number(budget_nums[-1])
        else:
            amount = 0.0

        func = classify_item(code, description)

        all_items.append({
            'code': code,
            'description': description,
            'amount': amount,
            'function': func,
        })

    pdf.close()

    # Phase 6: Deduplicate exact (code, amount) pairs
    seen = set()
    unique_items = []
    for item in all_items:
        key = (item['code'], item['amount'])
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    all_items = unique_items

    # Aggregate by function
    totals = defaultdict(float)
    for item in all_items:
        totals[item['function']] += item['amount']

    if debug:
        for item in all_items:
            print(f"  {item['code']:<18s} -> {item['function']:<20s} "
                  f"{item['amount']:>12,.2f}  | {item['description'][:80]}")

    return {
        'items': all_items,
        'totals': dict(totals),
        'pw_total_pdf': pw_total_pdf,
    }


def find_pw_total_from_text(pdf_path: str) -> float:
    """Fallback: scan for Total Public Works in payments section."""
    pdf = pdfplumber.open(pdf_path)
    # Look for the LAST occurrence of "Total Public Works" (expenditure one)
    last_total = 0.0
    for page in pdf.pages:
        text = page.extract_text() or ""
        text = re.sub(r'\(cid:\d+\)', '', text)
        lines = text.split('\n')
        for idx, line in enumerate(lines):
            if re.search(r'Total\s+Public\s+Works', line, re.IGNORECASE):
                nums = re.findall(r'[\d,]+\.\d{2}', line)
                if nums:
                    last_total = parse_indian_number(nums[-1])
                elif idx > 0:
                    nums = re.findall(r'[\d,]+\.\d{2}', lines[idx - 1])
                    if nums:
                        last_total = parse_indian_number(nums[-1])
    pdf.close()
    return last_total


def update_json(corp: str, totals: dict, pw_total_pdf: float, items: list):
    """Update the existing corporation JSON with split Public Works data."""
    json_path = JSON_DIR / f"{corp}.json"
    with open(json_path, 'r') as f:
        data = json.load(f)

    total_expenditure = data['summary']['total_expenditure']
    old_pw_amount = data['by_department']['Public Works']

    functions = ['roads-bridges', 'street-lighting', 'water-supply',
                 'sewerage-drainage', 'fire-services']
    for fn in functions:
        if fn not in totals:
            totals[fn] = 0.0

    extracted_total = sum(totals.values())

    # Scale proportionally to match the official PW department total
    scale_factor = 1.0
    if extracted_total > 0 and abs(extracted_total - old_pw_amount) > 1:
        scale_factor = old_pw_amount / extracted_total

    scaled_totals = {}
    for fn in functions:
        scaled_totals[fn] = round(totals[fn] * scale_factor, 2)

    # Adjust rounding so sum matches exactly
    diff = round(old_pw_amount - sum(scaled_totals.values()), 2)
    if abs(diff) > 0.01:
        scaled_totals['roads-bridges'] = round(scaled_totals['roads-bridges'] + diff, 2)

    # Rebuild by_function: remove old roads-bridges, add split entries
    old_by_function = {k: v for k, v in data['by_function'].items()
                       if k not in functions}

    new_by_function = {}
    for key, val in old_by_function.items():
        new_by_function[key] = val
        if key == 'urban-planning':
            for fn in functions:
                amt = scaled_totals[fn]
                share = round(amt / total_expenditure, 4) if total_expenditure > 0 else 0
                new_by_function[fn] = {
                    'amount': amt,
                    'share': share,
                    'yoy_growth': 0
                }

    # Safety: ensure all functions present
    for fn in functions:
        if fn not in new_by_function:
            amt = scaled_totals[fn]
            share = round(amt / total_expenditure, 4) if total_expenditure > 0 else 0
            new_by_function[fn] = {
                'amount': amt,
                'share': share,
                'yoy_growth': 0
            }

    data['by_function'] = new_by_function

    # Add public_works_split section
    data['public_works_split'] = {
        'total_public_works': old_pw_amount,
        'extracted_line_items_total': round(extracted_total, 2),
        'scale_factor': round(scale_factor, 6),
        'breakdown': {fn: scaled_totals[fn] for fn in functions},
        'line_item_count': len(items),
        'methodology': (
            'Line items extracted from PDF budget book using pdfplumber. '
            'Classification by budget code sub-groups (05-22XXXX for R&M, '
            '05-40XXXX for capital works) and English keyword matching. '
            'Amounts scaled proportionally to match the official Public Works '
            'department total where extraction total differs.'
        ),
    }

    data['note'] = (
        'Public Works split into roads-bridges, street-lighting, water-supply, '
        'sewerage-drainage, and fire-services based on line-item extraction '
        'from the budget PDF. See public_works_split for methodology.'
    )

    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return scaled_totals


def main():
    print("=" * 100)
    print("PUBLIC WORKS SPLIT — GBA Corporation Budgets 2026-27")
    print("=" * 100)

    all_results = {}

    for corp in CORPS:
        pdf_path = RAW_DIR / f"{corp}.pdf"
        if not pdf_path.exists():
            print(f"\n  WARNING: {pdf_path} not found, skipping")
            continue

        print(f"\n{'─' * 80}")
        print(f"  Processing: {corp.upper()}")
        print(f"{'─' * 80}")

        result = extract_public_works(str(pdf_path), debug=True)

        # Fallback for PW total
        if result['pw_total_pdf'] == 0:
            result['pw_total_pdf'] = find_pw_total_from_text(str(pdf_path))

        # Read existing JSON for PW total
        json_path = JSON_DIR / f"{corp}.json"
        with open(json_path) as f:
            existing = json.load(f)
        json_pw = existing['by_department']['Public Works']

        print(f"\n  PDF Total Public Works: {result['pw_total_pdf']:>12,.2f}")
        print(f"  JSON Public Works:      {json_pw:>12,.2f}")
        print(f"  Extracted items total:  {sum(result['totals'].values()):>12,.2f}")
        print(f"  Line items found:       {len(result['items'])}")

        print(f"\n  Raw extraction by function:")
        for fn, amt in sorted(result['totals'].items(), key=lambda x: -x[1]):
            print(f"    {fn:<25s} {amt:>12,.2f}")

        # Update JSON
        scaled = update_json(corp, result['totals'], result['pw_total_pdf'],
                             result['items'])

        print(f"\n  Scaled breakdown (matching JSON total {json_pw:,.2f}):")
        for fn, amt in sorted(scaled.items(), key=lambda x: -x[1]):
            pct = (amt / json_pw * 100) if json_pw > 0 else 0
            print(f"    {fn:<25s} {amt:>12,.2f}  ({pct:5.1f}%)")

        all_results[corp] = {
            'json_pw_total': json_pw,
            'scaled': scaled,
            'items_count': len(result['items']),
        }

    # Summary table
    functions = ['roads-bridges', 'street-lighting', 'water-supply',
                 'sewerage-drainage', 'fire-services']

    print(f"\n\n{'=' * 100}")
    print("SUMMARY TABLE — Public Works Split (in Lakhs)")
    print(f"{'=' * 100}")

    header = f"{'Corporation':<12s} {'PW Total':>12s}"
    for fn in functions:
        header += f" {fn:>18s}"
    print(header)
    print("─" * len(header))

    for corp in CORPS:
        if corp not in all_results:
            continue
        r = all_results[corp]
        row = f"{corp.upper():<12s} {r['json_pw_total']:>12,.2f}"
        for fn in functions:
            row += f" {r['scaled'].get(fn, 0):>18,.2f}"
        print(row)

    print("─" * len(header))
    total_pw = sum(r['json_pw_total'] for r in all_results.values())
    totals_row = f"{'TOTAL':<12s} {total_pw:>12,.2f}"
    for fn in functions:
        total_fn = sum(r['scaled'].get(fn, 0) for r in all_results.values())
        totals_row += f" {total_fn:>18,.2f}"
    print(totals_row)

    pct_row = f"{'% share':<12s} {'100.0%':>12s}"
    for fn in functions:
        total_fn = sum(r['scaled'].get(fn, 0) for r in all_results.values())
        pct = (total_fn / total_pw * 100) if total_pw > 0 else 0
        pct_row += f" {pct:>17.1f}%"
    print(pct_row)

    print(f"\n  All {len(all_results)} JSON files updated in: {JSON_DIR}")
    print("  Done.\n")


if __name__ == '__main__':
    main()
