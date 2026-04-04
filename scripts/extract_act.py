#!/usr/bin/env python3
"""
extract_act.py — Extract the Greater Bengaluru Governance Act from PDF into structured JSON.

Parses the GBA Act PDF (36_of_2025_(e).pdf, 166 pages) into:
  - Chapter-level structure with sections
  - Schedule I: Functions of City Corporations (core, general, sector-wise)

Usage:
  python3 scripts/extract_act.py
  python3 scripts/extract_act.py path/to/act.pdf
  python3 scripts/extract_act.py --output data/bengaluru/act/gba-act.json
"""

import argparse
import json
import os
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: PyMuPDF not installed. Run: pip install pymupdf", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PDF = "data/raw/bengaluru/act/36_of_2025_(e).pdf"
DEFAULT_OUTPUT = "data/bengaluru/act/gba-act.json"

# Roman numeral mapping for chapter parsing
ROMAN_MAP = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20,
    "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24, "XXV": 25,
    "XXVI": 26, "XXVII": 27, "XXVIII": 28, "XXIX": 29, "XXX": 30,
}

# Roman numeral pattern (matches I through XXX)
ROMAN_RE = r"(?:XXX|XXIX|XXVIII|XXVII|XXVI|XXV|XXIV|XXIII|XXII|XXI|XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)"

# Lowercase roman numerals for schedule items
LOWER_ROMAN_MAP = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: str) -> list[str]:
    """Extract text from every page of the PDF. Returns list of page texts (0-indexed)."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return pages


def clean_text(text: str) -> str:
    """Normalise whitespace in extracted text while preserving paragraph breaks."""
    # Collapse runs of spaces/tabs within lines
    text = re.sub(r"[ \t]+", " ", text)
    # Normalise line endings
    text = re.sub(r"\r\n?", "\n", text)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def join_page_range(pages: list[str], start: int, end: int) -> str:
    """Join text from pages[start] through pages[end-1] (0-indexed)."""
    return "\n\n".join(pages[start:end])


# ---------------------------------------------------------------------------
# Chapter parsing
# ---------------------------------------------------------------------------

def find_chapters(pages: list[str]) -> list[dict]:
    """
    Detect CHAPTER headings across all pages.

    The PDF uses inconsistent formats for chapter headings:
      "CHAPTER - I\nPRELIMINARY"
      "CHAPTER-IV\nCITY CORPORATION AUTHORITIES"
      "CHAPTER VII\nMAYOR, DEPUTY MAYOR, ..."
      "Chapter - XIV\nTAXATION"
      "CHAPTER XXV PENALTIES"

    Returns a list of dicts:
      { "number": "I", "title": "...", "page_start": N (1-indexed), "page_end": None }

    page_end is filled in by a second pass once we know where the next chapter starts.
    """
    # Step 1: Match "CHAPTER" + optional dash/spaces + roman numeral.
    # The title may follow on the SAME line or on the NEXT line(s).
    chapter_num_re = re.compile(
        r"CHAPTER[\s\-–—]*(" + ROMAN_RE + r")\b",
        re.IGNORECASE,
    )

    chapters = []
    seen = set()

    for page_idx, text in enumerate(pages):
        # Skip TOC pages (first ~15 pages contain table of contents references)
        if page_idx < 15:
            continue

        for m in chapter_num_re.finditer(text):
            num = m.group(1).strip().upper()

            # Deduplicate: same chapter may appear in TOC and body
            if num in seen:
                continue

            # Extract the title: look at text after the chapter number
            after = text[m.end():]
            # The title is typically ALL CAPS on the same line or next line(s)
            # It may be separated by newlines, dashes, #/## markdown from PageIndex
            title = ""
            title_lines = []
            for line in after.split("\n"):
                line = line.strip()
                # Skip empty lines, markdown headings markers, short noise
                line = re.sub(r"^#+\s*", "", line)  # remove markdown heading prefix
                line = line.strip()
                if not line:
                    if title_lines:
                        break  # blank line after title means end of title
                    continue
                # Title lines are typically ALL CAPS or Title Case
                # Stop at section numbers like "1. Short title..." or "(1) The..."
                if re.match(r"\d+\.\s+[A-Z]", line):
                    break
                if re.match(r"\(\d+\)", line):
                    break
                # Collect title-like lines (largely uppercase)
                upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line.replace(" ", "")), 1)
                if upper_ratio > 0.5 or not title_lines:
                    title_lines.append(line)
                else:
                    break
                # Limit to 2 lines of title
                if len(title_lines) >= 2:
                    break

            title = " ".join(title_lines)
            # Clean up: remove trailing punctuation, dashes, normalize whitespace
            title = re.sub(r"[\n\r]+", " ", title)
            title = re.sub(r"\s+", " ", title).strip()
            title = title.strip("-–—:. ")
            # Remove any leading/trailing non-alpha
            title = re.sub(r"^[^A-Za-z]+", "", title)
            title = re.sub(r"[^A-Za-z)]+$", "", title)

            if not title:
                title = f"Chapter {num}"

            seen.add(num)
            chapters.append({
                "number": num,
                "title": title,
                "page_start": page_idx + 1,  # 1-indexed
                "page_end": None,
            })

    # Sort by page_start to ensure correct ordering
    chapters.sort(key=lambda c: c["page_start"])

    # Fill in page_end for each chapter (extends to the start of next chapter)
    for i, ch in enumerate(chapters):
        if i + 1 < len(chapters):
            ch["page_end"] = chapters[i + 1]["page_start"]
        else:
            # Last chapter ends at start of schedules or end of document
            ch["page_end"] = len(pages)

    return chapters


def extract_sections_for_chapter(pages: list[str], chapter: dict) -> list[dict]:
    """
    Extract numbered sections within a chapter's page range.

    Sections look like: "1. Short title, extent and commencement.—"
    or "42. Powers of the Authority.—(1) ..."
    """
    start_idx = chapter["page_start"] - 1  # convert to 0-indexed
    end_idx = chapter["page_end"] - 1 if chapter["page_end"] else len(pages)
    text = join_page_range(pages, start_idx, end_idx)
    text = clean_text(text)

    # Match section headings: number followed by period, then title text ending with .- or .— or .–
    # The PDF renders these as:
    #   "1. Short title, extent and commencement.- (1) This Act..."
    #   "110. General powers of the City Corporation.\n(1) Subject to..."
    #   "111. Powers and Functions of the City Corporation.\n(1) The City..."
    #   "303. Granting of license.- (1) The Commissioner..."
    # The title may contain internal periods (e.g., "etc.") so we match up to ".-" or ".\n"
    # We use a two-step approach: match the section number + first part of title robustly
    section_re = re.compile(
        r"(?:^|\n)\s*(\d+)\.\s+"                    # section number: "123. "
        r"([A-Z][A-Za-z,\s\-–—'()&/]+?)"           # title: starts uppercase, letters/spaces/punct
        r"(?:\.\s*[-–—]\s|\.\s*\n|\.\s*$)",          # ends with .- or .\n or . at end of text
        re.MULTILINE,
    )

    sections = []
    matches = list(section_re.finditer(text))

    for i, m in enumerate(matches):
        sec_num = m.group(1).strip()
        sec_title = m.group(2).strip()
        # Clean up title
        sec_title = re.sub(r"\s+", " ", sec_title).strip()

        # Extract section text: from this match to the next section (or end of chapter text)
        start_pos = m.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sec_text = text[start_pos:end_pos].strip()

        # Trim to reasonable length for JSON (first 500 chars as preview)
        if len(sec_text) > 500:
            sec_text = sec_text[:500] + "..."

        sections.append({
            "number": sec_num,
            "title": sec_title,
            "text": sec_text,
        })

    return sections


# ---------------------------------------------------------------------------
# Schedule I parsing — Functions of City Corporations
# ---------------------------------------------------------------------------

def find_schedule_pages(pages: list[str]) -> tuple[int, int]:
    """
    Find the page range for SCHEDULE I in the PDF.
    Returns (start_0indexed, end_0indexed) — end is exclusive.
    """
    start = None
    end = None

    for i, text in enumerate(pages):
        upper = text.upper()
        # Look for "SCHEDULE I" or "SCHEDULE-I" or "SCHEDULE – I" or "FIRST SCHEDULE"
        # The PDF uses an en-dash: "SCHEDULE – I"
        if start is None and re.search(r"SCHEDULE[\s\-–—]*I\b", upper):
            # Confirm it's the functions schedule (not a passing reference to schedule)
            # Either has "function"/"core" keywords or is in the expected page range (>150)
            if "FUNCTION" in upper or "CORE" in upper or "CORPORATION" in upper or i >= 155:
                start = i

        # Look for SCHEDULE II to mark the end
        if start is not None and i > start and re.search(r"SCHEDULE[\s\-–—]*II\b", upper):
            end = i
            break

    if start is None:
        return (-1, -1)

    if end is None:
        # Default: schedule goes to near end of document
        end = min(start + 6, len(pages))

    return (start, end)


def parse_roman_item(line: str) -> tuple[str, str] | None:
    """
    Parse a line like "(iii) Urban planning including town planning"
    Returns (roman_numeral, text) or None if no match.
    """
    m = re.match(r"\(([ivxlc]+)\)\s+(.*)", line.strip(), re.IGNORECASE)
    if m:
        return (m.group(1).lower(), m.group(2).strip())
    return None


def parse_alpha_item(line: str) -> tuple[str, str] | None:
    """
    Parse a line like "(a) Planned development of new areas..."
    Returns (letter, text) or None if no match.
    """
    m = re.match(r"\(([a-z])\)\s+(.*)", line.strip())
    if m:
        return (m.group(1), m.group(2).strip())
    return None


def parse_schedule_i(pages: list[str]) -> dict:
    """
    Parse Schedule I — Functions of City Corporations into structured data.

    The schedule has three numbered subsections:
      (1) Core Functions — 18 items numbered (i) through (xviii)
      (2) General Functions — 15 items numbered (i) through (xv)
      (3) Sector-wise Functions — 12 sectors numbered (i) through (xii),
          each with sub-items (a), (b), (c)...

    Returns a dict with keys: title, section_reference, core_functions,
    general_functions, sector_functions.
    """
    sched_start, sched_end = find_schedule_pages(pages)

    result = {
        "title": "Functions of City Corporations",
        "section_reference": "111",
        "core_functions": [],
        "general_functions": [],
        "sector_functions": [],
    }

    if sched_start < 0:
        print("  Warning: Could not locate Schedule I in the PDF.", file=sys.stderr)
        return result

    # Join all schedule text
    raw_text = join_page_range(pages, sched_start, sched_end)
    text = clean_text(raw_text)

    # Split into the three subsections using "(1)", "(2)", "(3)" markers
    # These appear as standalone markers indicating core, general, sector
    # Try to split on the category headers
    core_text = ""
    general_text = ""
    sector_text = ""

    # Strategy: find the three category boundaries
    # Look for "(1)" followed by "Core Functions" or the first (i) item
    # Look for "(2)" followed by "General Functions"
    # Look for "(3)" followed by "Sector" or "sector-wise"

    # Pattern for the category headers
    # The PDF renders these as "(1) Core Functions.-" or "(1) Core Functions.-\n"
    cat1_re = re.compile(r"\(1\)\s*Core\s+Functions\s*[.\-–—]*", re.IGNORECASE)
    cat2_re = re.compile(r"\(2\)\s*General\s+Functions\s*[.\-–—]*", re.IGNORECASE)
    cat3_re = re.compile(r"\(3\)\s*Sector[\s\-–—]*wise\s+[Ff]unctions\s*[.\-–—]*", re.IGNORECASE)

    m1 = cat1_re.search(text)
    m2 = cat2_re.search(text)
    m3 = cat3_re.search(text)

    if m1 and m2 and m3:
        core_text = text[m1.end():m2.start()]
        general_text = text[m2.end():m3.start()]
        sector_text = text[m3.end():]
    else:
        # Fallback: try splitting on just "(1)", "(2)", "(3)" as standalone markers
        # Sometimes the PDF renders these without the category name right after
        parts = re.split(r"\n\s*\(([123])\)\s*", text)
        # parts will be: [before, "1", text1, "2", text2, "3", text3, ...]
        section_map = {}
        i = 1
        while i < len(parts) - 1:
            key = parts[i].strip()
            val = parts[i + 1]
            section_map[key] = val
            i += 2

        core_text = section_map.get("1", "")
        general_text = section_map.get("2", "")
        sector_text = section_map.get("3", "")

    # --- Parse Core Functions ---
    result["core_functions"] = _parse_roman_list(core_text)

    # --- Parse General Functions ---
    result["general_functions"] = _parse_roman_list(general_text)

    # --- Parse Sector-wise Functions ---
    result["sector_functions"] = _parse_sector_list(sector_text)

    return result


def _parse_roman_list(text: str) -> list[dict]:
    """
    Parse a list of items numbered (i), (ii), ... (xviii) etc.
    Returns list of {"number": "i", "text": "..."}.
    """
    items = []
    # Match each (roman) item. Capture everything until the next (roman) or end of text.
    item_re = re.compile(
        r"\(([ivxlc]+)\)\s+(.*?)(?=\n\s*\([ivxlc]+\)\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    for m in item_re.finditer(text):
        num = m.group(1).lower()
        item_text = m.group(2).strip()
        # Clean up: collapse newlines and extra spaces
        item_text = re.sub(r"\s+", " ", item_text).strip()
        # Remove trailing punctuation artifacts
        item_text = item_text.rstrip(";.,")
        if item_text:
            items.append({"number": num, "text": item_text})

    return items


def _parse_sector_list(text: str) -> list[dict]:
    """
    Parse the sector-wise functions. Each sector is numbered (i)-(xii) with a title,
    and contains sub-items (a), (b), (c)...

    From the actual PDF, sectors look like:
      "(i) Urban Planning including Town Planning:\n(a) Planned development..."
      "(vi) Public Works: Construct and maintain the roads..."
      "(xii) Disaster Relief: Maintain relief centres..."

    The delimiter between title and body is typically ":" or ":\n".

    Returns list of:
      {"number": "i", "title": "Urban Planning including Town Planning",
       "items": [{"sub": "a", "text": "..."}]}
    """
    sectors = []

    # First, split text into per-sector chunks using the (roman) pattern.
    # Each chunk starts at a (roman) marker and extends to the next one.
    sector_boundaries = list(re.finditer(
        r"\(([ivxlc]+)\)\s+",
        text,
        re.IGNORECASE,
    ))

    for idx, m in enumerate(sector_boundaries):
        num = m.group(1).lower()

        # Only process valid roman numerals in our expected range
        if num not in LOWER_ROMAN_MAP:
            continue

        # Get the text for this sector (up to the next sector or end)
        start = m.end()
        end = sector_boundaries[idx + 1].start() if idx + 1 < len(sector_boundaries) else len(text)
        chunk = text[start:end].strip()

        if not chunk:
            continue

        # Split title from body. Title ends at ":" or ":\n" or at first "(a)"
        # Pattern: title text followed by colon, then body
        title_body = re.match(
            r"(.*?)\s*:\s*(.*)",
            chunk,
            re.DOTALL,
        )

        if title_body:
            title = title_body.group(1).strip()
            body = title_body.group(2).strip()
        else:
            # No colon found; treat whole chunk as title with no sub-items
            title = chunk.strip()
            body = ""

        # Clean title: collapse whitespace, remove trailing punctuation
        title = re.sub(r"\s+", " ", title).strip().rstrip(".-–—:;,")

        # Parse sub-items (a), (b), ... from body
        sub_items = []
        if body:
            sub_re = re.compile(
                r"\(([a-z])\)\s+(.*?)(?=\s*\([a-z]\)\s+|\Z)",
                re.DOTALL,
            )
            for sm in sub_re.finditer(body):
                sub_letter = sm.group(1)
                sub_text = sm.group(2).strip()
                sub_text = re.sub(r"\s+", " ", sub_text).strip().rstrip(";.,")
                if sub_text:
                    sub_items.append({"sub": sub_letter, "text": sub_text})

            # If no (a)/(b) sub-items found, treat body as a single item
            if not sub_items:
                body_clean = re.sub(r"\s+", " ", body).strip().rstrip(";.,")
                if body_clean:
                    sub_items.append({"sub": "a", "text": body_clean})

        if title:
            sectors.append({
                "number": num,
                "title": title,
                "items": sub_items,
            })

    # If the boundary-based approach found nothing, try fallback line-based parse
    if not sectors:
        sectors = _parse_sector_list_fallback(text)

    return sectors


def _parse_sector_list_fallback(text: str) -> list[dict]:
    """
    Fallback parser for sector functions using a line-by-line approach.
    """
    sectors = []
    lines = text.split("\n")
    current_sector = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if this is a new sector heading: (i) Title
        rom = re.match(r"\(([ivxlc]+)\)\s+(.+)", line, re.IGNORECASE)
        if rom:
            num = rom.group(1).lower()
            rest = rom.group(2).strip()

            # Is this a sector heading (title-case, often followed by .— on same/next line)?
            # Heuristic: if the text starts with a capital and is short-ish, treat as title
            title_match = re.match(r"([A-Z][A-Za-z\s,&\-/()]+?)(?:\.\s*[—\-:]|$)", rest)
            if title_match and num in LOWER_ROMAN_MAP:
                if current_sector:
                    sectors.append(current_sector)
                title = title_match.group(1).strip()
                current_sector = {"number": num, "title": title, "items": []}
                # Check if there's remaining text after the title
                remainder = rest[title_match.end():].strip()
                if remainder:
                    alpha = parse_alpha_item(remainder)
                    if alpha:
                        current_sector["items"].append({"sub": alpha[0], "text": alpha[1]})
                continue

        # Check for sub-item: (a) text
        alpha = parse_alpha_item(line)
        if alpha and current_sector is not None:
            current_sector["items"].append({"sub": alpha[0], "text": alpha[1]})
            continue

        # Otherwise append to last sub-item text if we're inside a sector
        if current_sector and current_sector["items"]:
            current_sector["items"][-1]["text"] += " " + line

    if current_sector:
        sectors.append(current_sector)

    # Clean up
    for s in sectors:
        for item in s["items"]:
            item["text"] = re.sub(r"\s+", " ", item["text"]).strip().rstrip(";.,")

    return sectors


# ---------------------------------------------------------------------------
# Main assembly
# ---------------------------------------------------------------------------

def build_act_json(pdf_path: str) -> dict:
    """
    Build the full structured JSON from the GBA Act PDF.
    """
    print(f"Reading PDF: {pdf_path}")
    pages = extract_pages(pdf_path)
    total_pages = len(pages)
    print(f"  Total pages: {total_pages}")

    # --- Extract chapters ---
    print("Parsing chapters...")
    chapters = find_chapters(pages)
    print(f"  Found {len(chapters)} chapters")

    # Extract sections within each chapter
    for ch in chapters:
        ch["sections"] = extract_sections_for_chapter(pages, ch)
        print(f"  Chapter {ch['number']:>5s}: {ch['title'][:50]:<50s}  "
              f"(pp {ch['page_start']}-{ch['page_end']}, "
              f"{len(ch['sections'])} sections)")

    # --- Extract Schedule I ---
    print("Parsing Schedule I (Functions of City Corporations)...")
    schedule_i = parse_schedule_i(pages)
    print(f"  Core functions:    {len(schedule_i['core_functions'])}")
    print(f"  General functions: {len(schedule_i['general_functions'])}")
    print(f"  Sector functions:  {len(schedule_i['sector_functions'])}")

    # --- Assemble output ---
    act = {
        "title": "The Greater Bengaluru Governance Act, 2024",
        "act_number": "36 of 2025",
        "pages": total_pages,
        "chapters": chapters,
        "schedules": {
            "I": schedule_i,
        },
    }

    return act


def print_summary(act: dict):
    """Print a human-readable summary of what was extracted."""
    print("\n" + "=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"Title:    {act['title']}")
    print(f"Act No:   {act['act_number']}")
    print(f"Pages:    {act['pages']}")
    print(f"Chapters: {len(act['chapters'])}")

    total_sections = sum(len(ch["sections"]) for ch in act["chapters"])
    print(f"Sections: {total_sections}")

    sched = act["schedules"]["I"]
    print(f"\nSchedule I — {sched['title']}:")
    print(f"  Core functions:    {len(sched['core_functions'])}")
    print(f"  General functions: {len(sched['general_functions'])}")
    print(f"  Sector functions:  {len(sched['sector_functions'])}")

    total_sub = sum(len(s.get("items", [])) for s in sched["sector_functions"])
    print(f"  Sector sub-items:  {total_sub}")

    # List sector titles
    if sched["sector_functions"]:
        print("\n  Sector-wise function areas:")
        for s in sched["sector_functions"]:
            n_items = len(s.get("items", []))
            print(f"    ({s['number']}) {s['title']} — {n_items} items")

    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract the GBA Act from PDF into structured JSON.",
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=DEFAULT_PDF,
        help=f"Path to the GBA Act PDF (default: {DEFAULT_PDF})",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    # Resolve paths relative to project root (one level up from scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    pdf_path = args.pdf
    if not os.path.isabs(pdf_path):
        pdf_path = os.path.join(project_root, pdf_path)

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(project_root, output_path)

    # Check PDF exists
    if not os.path.isfile(pdf_path):
        print(f"Error: PDF not found at {pdf_path}", file=sys.stderr)
        print(f"Download the GBA Act PDF to: {pdf_path}", file=sys.stderr)
        print(f"  Expected file: 36_of_2025_(e).pdf (166 pages, English version)", file=sys.stderr)
        sys.exit(1)

    # Run extraction
    act = build_act_json(pdf_path)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Write JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(act, f, indent=2, ensure_ascii=False)

    print(f"\nOutput written to: {output_path}")
    print_summary(act)


if __name__ == "__main__":
    main()
