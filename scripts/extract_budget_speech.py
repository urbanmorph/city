#!/usr/bin/env python3
"""Extract text from a Bengaluru City Corporation Budget Speech PDF and
enrich the corresponding ``{corp}-projects.json`` file with derived fields.

The BSCC 2026-27 speech is bilingual (Kannada pages 1-38, English pages
41-71, budget-table pages 39-40 and 72-73). Both ``pdfplumber`` and
``PyMuPDF`` fail on Kannada because the PDF uses font-remapped CID glyphs
(pdfplumber emits ``(cid:NNNN)`` tokens; PyMuPDF emits mojibake Unicode).
OCR with Tesseract (``kan+eng``) is the most reliable path and also picks
up the English section cleanly.

Usage modes:

* ``--mode raw``  (default)
    Run pdfplumber + PyMuPDF + Tesseract OCR; write the three raw-text
    files into ``out-dir``.

* ``--mode enrich --corp south``
    Read the existing ``{corp}-projects.json`` (hand-curated for South),
    then add additive fields (``section``, ``timeline``,
    ``beneficiary_count``, ``beneficiary_type``, ``location``, ``ward``,
    ``is_continuing``, ``linked_goal_ids``, ``verbatim_quote_local``)
    to each project plus top-level ``speech_total_pages``, ``sections``,
    ``budget_at_a_glance``, ``commissioner_intro_text`` and
    ``commissioner_closing_text``. Project IDs are preserved.

Prerequisites:
    pip install pdfplumber pymupdf
    brew install tesseract
    # Kannada traineddata (not in Homebrew):
    curl -L https://github.com/tesseract-ocr/tessdata_best/raw/main/kan.traineddata \\
        -o /tmp/kan.traineddata
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber


CID_RE = re.compile(r"\(cid:\d+\)")


def extract_pdfplumber(pdf_path: Path, out: Path) -> dict:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            raw = p.extract_text() or ""
            pages.append((i, raw, len(CID_RE.findall(raw))))
    with out.open("w", encoding="utf-8") as fh:
        for i, raw, cids in pages:
            fh.write(f"===== PAGE {i} (cids={cids}) =====\n{raw}\n\n")
    return {
        "file": str(out),
        "pages": len(pages),
        "total_chars": sum(len(r) for _, r, _ in pages),
        "total_cid_tokens": sum(c for *_, c in pages),
    }


def extract_pymupdf(pdf_path: Path, out: Path) -> dict:
    doc = fitz.open(pdf_path)
    pages = [(i + 1, doc[i].get_text() or "") for i in range(doc.page_count)]
    doc.close()
    with out.open("w", encoding="utf-8") as fh:
        for i, txt in pages:
            fh.write(f"===== PAGE {i} =====\n{txt}\n\n")
    return {
        "file": str(out),
        "pages": len(pages),
        "total_chars": sum(len(t) for _, t in pages),
    }


def ocr_pages(pdf_path: Path, workdir: Path, kan_traineddata: Path) -> tuple[list[tuple[int, str]], Path]:
    """Render each page to PNG at 300 DPI and OCR with Tesseract kan+eng."""
    workdir.mkdir(parents=True, exist_ok=True)

    # Set up a local TESSDATA_PREFIX dir with eng + osd + kan.
    tessdata_src = Path("/usr/local/share/tessdata")
    tessdata = workdir / "tessdata"
    tessdata.mkdir(exist_ok=True)
    for name in ("eng.traineddata", "osd.traineddata"):
        dst = tessdata / name
        if not dst.exists() and (tessdata_src / name).exists():
            shutil.copy(tessdata_src / name, dst)
    dst_kan = tessdata / "kan.traineddata"
    if not dst_kan.exists():
        shutil.copy(kan_traineddata, dst_kan)

    env = {**os.environ, "TESSDATA_PREFIX": str(tessdata)}

    doc = fitz.open(pdf_path)
    pages: list[tuple[int, str]] = []
    t0 = time.time()
    for i in range(doc.page_count):
        pn = i + 1
        img = workdir / f"p{pn:03d}.png"
        txt_stem = workdir / f"p{pn:03d}"
        if not img.exists():
            doc[i].get_pixmap(dpi=300).save(str(img))
        txt_path = Path(str(txt_stem) + ".txt")
        if not txt_path.exists():
            # Retry up to 3 times — tesseract occasionally exits 0 without
            # creating the output file (seen sporadically on multi-page runs).
            last_stderr = b""
            for attempt in range(3):
                r = subprocess.run(
                    ["tesseract", str(img), str(txt_stem), "-l", "kan+eng", "--psm", "6"],
                    capture_output=True, env=env,
                )
                last_stderr = r.stderr
                if r.returncode == 0 and txt_path.exists():
                    break
            else:
                raise RuntimeError(
                    f"tesseract failed on page {pn} after 3 attempts: {last_stderr!r}"
                )
        with open(txt_path, "r", encoding="utf-8") as fh:
            pages.append((pn, fh.read()))
        if pn % 10 == 0:
            print(f"  OCR {pn}/{doc.page_count} ({time.time()-t0:.1f}s)")
    doc.close()

    out = workdir.parent / "raw-ocr.txt"
    with out.open("w", encoding="utf-8") as fh:
        for pn, txt in pages:
            fh.write(f"===== PAGE {pn} =====\n{txt}\n\n")
    return pages, out


# ---------------------------------------------------------------------------
# Enrichment: add additive fields to an existing {corp}-projects.json
# ---------------------------------------------------------------------------


def _load_ocr_pages(ocr_path: Path) -> dict[int, str]:
    """Return {page_number: page_text} from an OCR raw-text file."""
    if not ocr_path.exists():
        return {}
    text = ocr_path.read_text(encoding="utf-8")
    parts = re.split(r"===== PAGE (\d+) =====\n", text)
    page_map: dict[int, str] = {}
    for i in range(1, len(parts), 2):
        try:
            pn = int(parts[i])
        except ValueError:
            continue
        page_map[pn] = parts[i + 1]
    return page_map


# Section-by-page table for South. Each entry is the speech section that
# starts on (or contains) the given English page. Page ranges are inclusive
# end and were derived from the speech-OCR section headings:
#   ADMINISTRATION (44-46), FINANCE AND ACCOUNTS (46-47), ADVERTISEMENT
#   (47-48), REVENUE (48-49), LAND ACQUISITION & e-DRC/e-TDR (49-50),
#   PUBLIC RELATIONS (50-51), URBAN PLANNING (51-52), PUBLIC WORKS
#   (52-57), LAKES DIVISION (57-59), EDUCATION (59-60), FORESTRY (60-62),
#   HORTICULTURE (62-63), HEALTH (63-64), SANITATION (64-66), ANIMAL
#   HUSBANDRY (66-67), WELFARE (67-71).
SOUTH_SECTIONS: list[dict] = [
    {"code": "administration", "title": "Administration", "title_local": "ಆಡಳಿತ", "page_start": 44, "page_end": 46},
    {"code": "finance-and-accounts", "title": "Finance and Accounts", "title_local": "ಹಣಕಾಸು ಮತ್ತು ಲೆಕ್ಕಪತ್ರ", "page_start": 46, "page_end": 47},
    {"code": "advertisement", "title": "Advertisement", "title_local": "ಜಾಹೀರಾತು", "page_start": 47, "page_end": 48},
    {"code": "revenue", "title": "Revenue", "title_local": "ಕಂದಾಯ", "page_start": 48, "page_end": 49},
    {"code": "land-acquisition", "title": "Land Acquisition and e-DRC/e-TDR", "title_local": "ಭೂಸ್ವಾಧಿನ ಮತ್ತು ಇ-ಡಿಆರ್‌ಸಿ / ಇ-ಟಿಡಿಆರ್‌", "page_start": 49, "page_end": 50},
    {"code": "public-relations", "title": "Public Relations", "title_local": "ಸಾರ್ವಜನಿಕ ಸಂಪರ್ಕ", "page_start": 50, "page_end": 51},
    {"code": "urban-planning", "title": "Urban Planning", "title_local": "ನಗರ ಯೋಜನೆ", "page_start": 51, "page_end": 52},
    {"code": "public-works", "title": "Public Works", "title_local": "ಸಾರ್ವಜನಿಕ ಕಾಮಗಾರಿಗಳು", "page_start": 52, "page_end": 57},
    {"code": "lakes-division", "title": "Lakes Division", "title_local": "ಕೆರೆಗಳ ವಿಭಾಗ", "page_start": 57, "page_end": 59},
    {"code": "education", "title": "Education", "title_local": "ಶಿಕ್ಷಣ", "page_start": 59, "page_end": 60},
    {"code": "forestry", "title": "Forestry", "title_local": "ಅರಣ್ಯ", "page_start": 60, "page_end": 62},
    {"code": "horticulture", "title": "Horticulture", "title_local": "ತೋಟಗಾರಿಕೆ", "page_start": 62, "page_end": 63},
    {"code": "health", "title": "Health", "title_local": "ಆರೋಗ್ಯ", "page_start": 63, "page_end": 64},
    {"code": "sanitation", "title": "Sanitation", "title_local": "ಸ್ವಚ್ಛತೆ", "page_start": 64, "page_end": 66},
    {"code": "animal-husbandry", "title": "Animal Husbandry", "title_local": "ಪಶು ಸಂಗೋಪನೆ", "page_start": 66, "page_end": 67},
    {"code": "welfare", "title": "Welfare", "title_local": "ಕಲ್ಯಾಣ", "page_start": 67, "page_end": 71},
]


# Central speech (English) — projects span pages 7-42. Section headings
# appear inline in the English PDF (pdfplumber with CID-glyphs); we pick
# the first page where each heading/topic becomes dominant.
CENTRAL_SECTIONS: list[dict] = [
    {"code": "revenue", "title": "Revenue", "title_local": "ಕಂದಾಯ", "page_start": 4, "page_end": 10},
    {"code": "administration", "title": "Administration", "title_local": "ಆಡಳಿತ", "page_start": 11, "page_end": 11},
    {"code": "public-relations", "title": "Public Relations", "title_local": "ಸಾರ್ವಜನಿಕ ಸಂಪರ್ಕ", "page_start": 12, "page_end": 12},
    {"code": "advertisement", "title": "Advertisement", "title_local": "ಜಾಹೀರಾತು", "page_start": 13, "page_end": 13},
    {"code": "land-acquisition", "title": "Land Acquisition", "title_local": "ಭೂಸ್ವಾಧಿನ", "page_start": 14, "page_end": 14},
    {"code": "urban-planning", "title": "Urban Planning", "title_local": "ನಗರ ಯೋಜನೆ", "page_start": 15, "page_end": 17},
    {"code": "finance-and-accounts", "title": "Finance and Accounts", "title_local": "ಹಣಕಾಸು ಮತ್ತು ಲೆಕ್ಕಪತ್ರ", "page_start": 18, "page_end": 19},
    {"code": "public-works", "title": "Public Works", "title_local": "ಸಾರ್ವಜನಿಕ ಕಾಮಗಾರಿಗಳು", "page_start": 20, "page_end": 26},
    {"code": "sanitation", "title": "Sanitation", "title_local": "ಸ್ವಚ್ಛತೆ", "page_start": 27, "page_end": 27},
    {"code": "lakes-division", "title": "Lakes Division", "title_local": "ಕೆರೆಗಳ ವಿಭಾಗ", "page_start": 28, "page_end": 29},
    {"code": "health", "title": "Health", "title_local": "ಆರೋಗ್ಯ", "page_start": 30, "page_end": 32},
    {"code": "animal-husbandry", "title": "Animal Husbandry", "title_local": "ಪಶು ಸಂಗೋಪನೆ", "page_start": 33, "page_end": 34},
    {"code": "horticulture", "title": "Horticulture", "title_local": "ತೋಟಗಾರಿಕೆ", "page_start": 35, "page_end": 35},
    {"code": "forestry", "title": "Urban Forestry", "title_local": "ಅರಣ್ಯ", "page_start": 36, "page_end": 36},
    {"code": "welfare", "title": "Social Welfare", "title_local": "ಕಲ್ಯಾಣ", "page_start": 37, "page_end": 39},
    {"code": "education", "title": "Public Education", "title_local": "ಶಿಕ್ಷಣ", "page_start": 40, "page_end": 42},
]


# East speech (English) — projects span pages 45-79. English section
# headings are prominent on each page of the English half (pp 45+).
EAST_SECTIONS: list[dict] = [
    {"code": "revenue", "title": "Revenue", "title_local": "ಕಂದಾಯ", "page_start": 45, "page_end": 48},
    {"code": "advertisement", "title": "Advertisement", "title_local": "ಜಾಹೀರಾತು", "page_start": 49, "page_end": 49},
    {"code": "land-acquisition", "title": "Land Acquisition and e-DRC/e-TDR", "title_local": "ಭೂಸ್ವಾಧಿನ ಮತ್ತು ಇ-ಡಿಆರ್‌ಸಿ / ಇ-ಟಿಡಿಆರ್‌", "page_start": 50, "page_end": 50},
    {"code": "urban-planning", "title": "Town Planning", "title_local": "ನಗರ ಯೋಜನೆ", "page_start": 51, "page_end": 51},
    {"code": "administration", "title": "Administration", "title_local": "ಆಡಳಿತ", "page_start": 52, "page_end": 53},
    {"code": "welfare", "title": "Welfare", "title_local": "ಕಲ್ಯಾಣ", "page_start": 54, "page_end": 57},
    {"code": "sanitation", "title": "Sanitation", "title_local": "ಸ್ವಚ್ಛತೆ", "page_start": 58, "page_end": 59},
    {"code": "public-relations", "title": "Public Relations", "title_local": "ಸಾರ್ವಜನಿಕ ಸಂಪರ್ಕ", "page_start": 60, "page_end": 60},
    {"code": "finance-and-accounts", "title": "Finance and Account Section", "title_local": "ಹಣಕಾಸು ಮತ್ತು ಲೆಕ್ಕಪತ್ರ", "page_start": 61, "page_end": 61},
    {"code": "public-works", "title": "Public Works", "title_local": "ಸಾರ್ವಜನಿಕ ಕಾಮಗಾರಿಗಳು", "page_start": 62, "page_end": 70},
    {"code": "lakes-division", "title": "Lakes Division", "title_local": "ಕೆರೆಗಳ ವಿಭಾಗ", "page_start": 71, "page_end": 72},
    {"code": "horticulture", "title": "Horticulture", "title_local": "ತೋಟಗಾರಿಕೆ", "page_start": 73, "page_end": 73},
    {"code": "health", "title": "Public Health", "title_local": "ಆರೋಗ್ಯ", "page_start": 74, "page_end": 74},
    {"code": "animal-husbandry", "title": "Animal Husbandry", "title_local": "ಪಶು ಸಂಗೋಪನೆ", "page_start": 75, "page_end": 75},
    {"code": "forestry", "title": "Forest", "title_local": "ಅರಣ್ಯ", "page_start": 76, "page_end": 77},
    {"code": "budget-summary", "title": "Bird's Eye View of Budget", "title_local": None, "page_start": 78, "page_end": 79},
]


# West speech (English) — projects span pages 4-41. Headings are inline in
# the English PDF.
WEST_SECTIONS: list[dict] = [
    {"code": "revenue", "title": "Revenue", "title_local": "ಕಂದಾಯ", "page_start": 4, "page_end": 6},
    {"code": "advertisement", "title": "Advertisement", "title_local": "ಜಾಹೀರಾತು", "page_start": 7, "page_end": 7},
    {"code": "land-acquisition", "title": "Land Acquisition and TDR", "title_local": "ಭೂಸ್ವಾಧಿನ ಮತ್ತು ಟಿಡಿಆರ್‌", "page_start": 8, "page_end": 8},
    {"code": "urban-planning", "title": "Urban Planning", "title_local": "ನಗರ ಯೋಜನೆ", "page_start": 9, "page_end": 11},
    {"code": "administration", "title": "Administration", "title_local": "ಆಡಳಿತ", "page_start": 12, "page_end": 13},
    {"code": "sanitation", "title": "Sanitation", "title_local": "ಸ್ವಚ್ಛತೆ", "page_start": 14, "page_end": 14},
    {"code": "welfare", "title": "Welfare", "title_local": "ಕಲ್ಯಾಣ", "page_start": 15, "page_end": 16},
    {"code": "public-relations", "title": "Public Relations", "title_local": "ಸಾರ್ವಜನಿಕ ಸಂಪರ್ಕ", "page_start": 17, "page_end": 17},
    {"code": "finance-and-accounts", "title": "Finance and Accounts Department", "title_local": "ಹಣಕಾಸು ಮತ್ತು ಲೆಕ್ಕಪತ್ರ", "page_start": 18, "page_end": 19},
    {"code": "horticulture", "title": "Horticulture", "title_local": "ತೋಟಗಾರಿಕೆ", "page_start": 20, "page_end": 21},
    {"code": "lakes-division", "title": "Lakes", "title_local": "ಕೆರೆಗಳ ವಿಭಾಗ", "page_start": 22, "page_end": 23},
    {"code": "education", "title": "Education Department", "title_local": "ಶಿಕ್ಷಣ", "page_start": 24, "page_end": 26},
    {"code": "health", "title": "Health", "title_local": "ಆರೋಗ್ಯ", "page_start": 27, "page_end": 30},
    {"code": "animal-husbandry", "title": "Animal Husbandry", "title_local": "ಪಶು ಸಂಗೋಪನೆ", "page_start": 31, "page_end": 31},
    {"code": "public-works", "title": "Public Works", "title_local": "ಸಾರ್ವಜನಿಕ ಕಾಮಗಾರಿಗಳು", "page_start": 32, "page_end": 41},
]


# North speech (English) — projects span pages 52-76. Headings appear in
# ALL-CAPS in the English portion of the PDF.
NORTH_SECTIONS: list[dict] = [
    {"code": "revenue", "title": "Revenue", "title_local": "ಕಂದಾಯ", "page_start": 52, "page_end": 52},
    {"code": "finance-and-accounts", "title": "Finance and Accounts", "title_local": "ಹಣಕಾಸು ಮತ್ತು ಲೆಕ್ಕಪತ್ರ", "page_start": 53, "page_end": 53},
    {"code": "advertisement", "title": "Advertisement", "title_local": "ಜಾಹೀರಾತು", "page_start": 54, "page_end": 54},
    {"code": "urban-planning", "title": "Town Planning", "title_local": "ನಗರ ಯೋಜನೆ", "page_start": 55, "page_end": 55},
    {"code": "administration", "title": "Administration", "title_local": "ಆಡಳಿತ", "page_start": 56, "page_end": 56},
    {"code": "welfare", "title": "Welfare", "title_local": "ಕಲ್ಯಾಣ", "page_start": 57, "page_end": 60},
    {"code": "public-relations", "title": "Public Relation", "title_local": "ಸಾರ್ವಜನಿಕ ಸಂಪರ್ಕ", "page_start": 61, "page_end": 61},
    {"code": "sanitation", "title": "Sanitation", "title_local": "ಸ್ವಚ್ಛತೆ", "page_start": 62, "page_end": 63},
    {"code": "forestry", "title": "Forest", "title_local": "ಅರಣ್ಯ", "page_start": 64, "page_end": 64},
    {"code": "horticulture", "title": "Horticulture", "title_local": "ತೋಟಗಾರಿಕೆ", "page_start": 65, "page_end": 65},
    {"code": "health", "title": "Health", "title_local": "ಆರೋಗ್ಯ", "page_start": 66, "page_end": 68},
    {"code": "animal-husbandry", "title": "Animal Husbandry", "title_local": "ಪಶು ಸಂಗೋಪನೆ", "page_start": 69, "page_end": 70},
    {"code": "public-works", "title": "Public Works", "title_local": "ಸಾರ್ವಜನಿಕ ಕಾಮಗಾರಿಗಳು", "page_start": 71, "page_end": 73},
    {"code": "lakes-division", "title": "Lakes", "title_local": "ಕೆರೆಗಳ ವಿಭಾಗ", "page_start": 74, "page_end": 74},
    {"code": "education", "title": "Education", "title_local": "ಶಿಕ್ಷಣ", "page_start": 75, "page_end": 76},
]


SECTIONS_BY_CORP: dict[str, list[dict]] = {
    "south": SOUTH_SECTIONS,
    "central": CENTRAL_SECTIONS,
    "east": EAST_SECTIONS,
    "west": WEST_SECTIONS,
    "north": NORTH_SECTIONS,
}


def _section_for_page(page: int | None, sections: list[dict]) -> dict | None:
    """Return the section dict that contains the given page number."""
    if page is None:
        return None
    for sec in sections:
        if sec["page_start"] <= page <= sec["page_end"]:
            return sec
    return None


# ---------------------------------------------------------------------------
# Per-project field inference
# ---------------------------------------------------------------------------

# Beneficiary patterns: (regex, beneficiary_type). Order matters — more
# specific patterns first (e.g. "72 wards" before "wards").
BENEFICIARY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b([\d,]+)\s+(?:newly\s+constituted\s+)?wards?\b", re.IGNORECASE), "wards"),
    (re.compile(r"\b([\d,]+)\s+lakh\s+(?:total\s+)?propert(?:y|ies)\b", re.IGNORECASE), "properties"),
    (re.compile(r"\b([\d,]+)\s+propert(?:y|ies)\b", re.IGNORECASE), "properties"),
    (re.compile(r"\b([\d,]+)\s+(?:direct[- ]wage\s+)?Pourakarmikas?\b", re.IGNORECASE), "pourakarmikas"),
    (re.compile(r"\b([\d,]+)\s+saplings?\b", re.IGNORECASE), "saplings"),
    (re.compile(r"\b([\d,]+)\s+(?:bamboo\s+)?(?:biodiversity\s+)?saplings?\b", re.IGNORECASE), "saplings"),
    (re.compile(r"\b([\d,]+)\s+(?:living\s+)?lakes?\b", re.IGNORECASE), "lakes"),
    (re.compile(r"\b([\d,]+)\s+(?:major\s+)?storm\s+water\s+drains?\b", re.IGNORECASE), "drains"),
    (re.compile(r"\b([\d,]+)\s+parks?\b", re.IGNORECASE), "parks"),
    (re.compile(r"\b([\d,]+)\s+UPHC", re.IGNORECASE), "uphcs"),
    (re.compile(r"\b([\d,]+)\s+Namma\s+Clinic", re.IGNORECASE), "namma_clinics"),
    (re.compile(r"\b([\d,]+)\s+(?:eligible\s+)?(?:dependent\s+)?families", re.IGNORECASE), "families"),
    (re.compile(r"\b([\d,]+)\s+students?\b", re.IGNORECASE), "students"),
    (re.compile(r"\b([\d,]+)\s+beneficiar", re.IGNORECASE), "beneficiaries"),
    (re.compile(r"\b([\d,]+)\s+stray\s+dogs?\b", re.IGNORECASE), "stray_dogs"),
    (re.compile(r"\b([\d,]+)\s+(?:dog\s+)?capacity", re.IGNORECASE), "dogs"),
    (re.compile(r"\bcapacity\s+of\s+([\d,]+)\s+dogs?\b", re.IGNORECASE), "dogs"),
    (re.compile(r"\b([\d,]+)\s+locations?\b", re.IGNORECASE), "locations"),
    (re.compile(r"\b([\d,]+)\s+recharge\s+pits?\b", re.IGNORECASE), "recharge_pits"),
]

# Place-name detection — common South Bengaluru localities mentioned in
# the speech. The match is case-sensitive (these are proper nouns) and
# returns the canonical form.
LOCATION_NAMES: list[str] = [
    "Banashankari", "Begur", "Hulimavu", "Chamarajpet", "Ejipura",
    "Govindarajanagar", "Yeshwanthpur", "Jayanagar", "Subramanyapura",
    "Bannerghatta", "Koramangala", "HSR Layout", "Madiwala", "Sarakki",
    "Ganapati Pura", "Kothanur", "Chunchaghatta", "Somasundarapalya",
    "Mangammanapalya", "Chikkabegur", "Gottigere", "Doddakammannahalli",
    "Vega City", "Raghavendra Swamy",
]


def _parse_int(s: str) -> int | None:
    s = s.replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        try:
            f = float(s)
            return int(f)
        except ValueError:
            return None


def _infer_beneficiary(text: str) -> tuple[int | None, str | None]:
    for pat, btype in BENEFICIARY_PATTERNS:
        m = pat.search(text)
        if m:
            n = _parse_int(m.group(1))
            if n is not None:
                return n, btype
    return None, None


def _infer_location(text: str) -> str | None:
    for name in LOCATION_NAMES:
        if name in text:
            return name
    return None


def _infer_timeline(text: str) -> str:
    """Best-effort timeline string. Default '2026-27' if nothing else."""
    low = text.lower()
    if re.search(r"three\s+years|3\s+years|next\s+3\s+years", low):
        return "3 years"
    if "phased" in low or "in phases" in low or "phase by phase" in low:
        return "phased"
    if "multi-year" in low or "multi year" in low:
        return "multi-year"
    if "2025-26" in text and "2026-27" in text:
        return "2025-26 to 2026-27"
    return "2026-27"


def _is_continuing(text: str) -> bool:
    low = text.lower()
    return any(
        kw in low
        for kw in (
            "ongoing", "continuing", "carryover", "carry over", "carry-over",
            "phase-2", "phase 2", "phase ii", "already been initiated",
            "already initiated", "already been", "expedite the ongoing",
            "in line with the requirements",
        )
    )


def _kannada_for_quote(quote: str | None, kan_pages: dict[int, str], project_page: int | None) -> str | None:
    """Best-effort: scan the Kannada speech pages near the project page for a
    short Kannada snippet that semantically matches the verbatim_quote.

    The Kannada speech occupies pages 1-38 and the English speech 41-71;
    typically the equivalent Kannada item is on (project_page - 36) to
    (project_page - 38). Without a robust translation lookup we simply
    fetch the first Kannada paragraph from that page that contains a
    distinctive numeric or proper-noun token from the English quote.
    """
    if not quote or not project_page:
        return None
    # Map English page → Kannada page (heuristic offset).
    target_kan_page = project_page - 36
    if target_kan_page < 1 or target_kan_page > 38:
        return None
    page = kan_pages.get(target_kan_page, "")
    if not page:
        return None
    # Try a couple of nearby pages too
    candidates: list[str] = [page]
    for delta in (-1, 1):
        nb = kan_pages.get(target_kan_page + delta, "")
        if nb:
            candidates.append(nb)
    # Look for a distinctive token in the English quote (number/proper noun)
    tokens: list[str] = []
    for m in re.finditer(r"[\d,]+\.?\d*", quote):
        tokens.append(m.group(0))
    for m in re.finditer(r"\b[A-Z][a-z]{4,}\b", quote):
        tokens.append(m.group(0))
    if not tokens:
        return None
    # Find the FIRST sentence in the candidate pages containing any of
    # the tokens; return the surrounding ~3 lines as the Kannada snippet.
    for cand in candidates:
        for tok in tokens:
            idx = cand.find(tok)
            if idx >= 0:
                # Grab a window
                start = max(0, idx - 200)
                end = min(len(cand), idx + 200)
                window = cand[start:end]
                # Trim to first paragraph break
                window = re.sub(r"\s+", " ", window).strip()
                # Only return if it has Kannada chars
                if re.search(r"[\u0C80-\u0CFF]", window):
                    return window[:300]
    return None


def _link_goals(projects: list[dict]) -> dict[str, list[str]]:
    """For each project ID, return a list of OTHER project IDs whose name
    appears in this project's description or verbatim_quote (rough
    cross-reference detection)."""
    by_id = {p["id"]: p for p in projects}
    # Only consider 'short' identifiers (3+ words from name) to avoid
    # spurious matches.
    keywords: dict[str, list[str]] = {}
    for p in projects:
        name = p.get("name", "")
        # Pick distinctive 2-3 word phrases (capitalised words)
        words = name.split()
        if len(words) >= 2:
            phrase = " ".join(words[:3]).strip(",.")
            keywords[p["id"]] = [phrase] if len(phrase) > 6 else []
        else:
            keywords[p["id"]] = []
    links: dict[str, list[str]] = {}
    for p in projects:
        haystack = (p.get("description") or "") + " " + (p.get("verbatim_quote") or "")
        found: list[str] = []
        for other_id, kws in keywords.items():
            if other_id == p["id"]:
                continue
            for kw in kws:
                if kw and kw in haystack:
                    found.append(other_id)
                    break
        links[p["id"]] = found
    return links


# ---------------------------------------------------------------------------
# Top-level fields
# ---------------------------------------------------------------------------


def _extract_intro_closing(eng_pages: dict[int, str]) -> tuple[str | None, str | None]:
    """Return (intro_text, closing_text) from the English speech pages.

    The Commissioner's intro starts on page 41-43; closing wraps on page 71.
    We grab the first 1-2 paragraphs of page 41 (skipping the
    'Hon'ble Deputy Chief Minister' salutation) and the last 1-2
    paragraphs before 'THANK YOU' on page 71.
    """
    intro = None
    closing = None
    p41 = eng_pages.get(41, "")
    p42 = eng_pages.get(42, "")
    if p41:
        # The intro 'I, Shri Ramesh K. N., I.A.S., Commissioner of the
        # Bengaluru South City Corporation...' is the substantive body.
        m = re.search(r"I[,\s]\s*Shri[^.]+?Commissioner.+?citizen", p41 + "\n" + p42, re.DOTALL)
        if m:
            intro = re.sub(r"\s+", " ", m.group(0)).strip()
            # Take up to ~700 chars
            if len(intro) > 700:
                intro = intro[:700].rsplit(".", 1)[0] + "."
        else:
            # Fall back to first ~600 chars after the page-1 salutation
            p = re.sub(r"BENGALURU SOUTH CITY CORPORATION BUDGET 2026-27", "", p41)
            p = re.sub(r"\s+", " ", p).strip()
            intro = p[:700]
    p71 = eng_pages.get(71, "")
    if p71:
        # Closing precedes 'THANK YOU' / 'JAI BENGALURU'. The OCR
        # sometimes mangles 'Let the Budget' into 'Let the budget'
        # (case) or breaks lines mid-sentence — be tolerant.
        m = re.search(
            r"(Let\s+the\s+Budget[^\n].+?)(?=THANK YOU|JAI BENGALURU|JAI KARNATAKA|$)",
            p71,
            re.DOTALL | re.IGNORECASE,
        )
        if m:
            closing = re.sub(r"\s+", " ", m.group(1)).strip()
        else:
            # Find the closing paragraph by looking for 'remembered'
            m2 = re.search(
                r"(remembered.+?)(?=THANK YOU|JAI BENGALURU|JAI KARNATAKA|$)",
                p71,
                re.DOTALL | re.IGNORECASE,
            )
            if m2:
                closing = re.sub(r"\s+", " ", m2.group(1)).strip()
    return intro, closing


def _extract_budget_at_a_glance(pages: dict[int, str]) -> dict | None:
    """Parse the 'Budget at a Glance' summary on pages 39-40 and 71-73.

    Returns a dict with receipts_total, expenditure_total,
    surplus_or_deficit, own_revenue_target, state_grants, central_grants,
    plus source_pages.
    """
    # Page 71 carries the narrative 'Budget at a Glance' paragraph.
    p71 = pages.get(71, "")
    out = {
        "receipts_total": None,
        "expenditure_total": None,
        "surplus_or_deficit": None,
        "own_revenue_target": None,
        "state_grants": None,
        "central_grants": None,
        "source_pages": [39, 40, 71, 72, 73],
    }
    # The narrative reads like:
    #   "the revenue from the Corporation's own resources, including the
    #   opening balance, is estimated to be 32,795.15 crores, while
    #   Central and State Government grants are expected to amount to
    #   927.31 crores. The total receipts are estimated at 73,826.43
    #   crores, and the total expenditure is estimated at 73,825.95
    #   crores, resulting in a surplus budget of 48.21 lakhs."
    text = re.sub(r"\s+", " ", p71)
    # Use a tolerant match — OCR commonly misreads the rupee symbol ₹
    # as the digit '3' or '7' (e.g. "₹3,826.43 crores" becomes
    # "73,826.43 crores"). Strip a leading '3' or '7' when the resulting
    # value would be unrealistically large for a single-corporation
    # budget (rough sanity: < 50,000 crore total receipts).
    def _grab(label_re: str) -> float | None:
        m = re.search(label_re + r"[^\d]*(\d[\d,]*\.?\d*)\s*crore", text, re.IGNORECASE)
        if not m:
            return None
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            return None
        # If it's way out of realistic bounds for a city corp budget
        # in crore (<15,000 cr per corp is the historical max), and the
        # leading digit is 3 or 7 (common ₹-misread by Tesseract), strip
        # the leading digit.
        if v > 15000 and raw[0] in ("3", "7") and len(raw) > 4:
            try:
                v = float(raw[1:])
            except ValueError:
                pass
        return v * 100  # crore → lakh
    out["own_revenue_target"] = _grab(r"own resources(?:[^.]*?including the opening balance)?\s*,\s*is estimated to be")
    grants_total = _grab(r"Central and State Government grants[^,]*expected to amount to")
    out["receipts_total"] = _grab(r"total receipts are estimated at")
    out["expenditure_total"] = _grab(r"total expenditure[^,]*estimated at")
    surplus_m = re.search(
        r"surplus budget of\s*[^\d]*(\d[\d,]*\.?\d*)\s*(crore|lakh)", text, re.IGNORECASE,
    )
    if surplus_m:
        try:
            v = float(surplus_m.group(1).replace(",", ""))
            if surplus_m.group(2).lower() == "crore":
                v *= 100
            out["surplus_or_deficit"] = v
        except ValueError:
            pass
    # Try to split state vs central from page 72/73 tables
    p72 = pages.get(72, "") + "\n" + pages.get(73, "")
    txt = re.sub(r"\s+", " ", p72)
    m_g = re.search(r"Government of India Grants\s*([\d,]+\.?\d*)", txt)
    if m_g:
        try:
            out["central_grants"] = float(m_g.group(1).replace(",", ""))
        except ValueError:
            pass
    m_s = re.search(r"Government of Karnataka Grants\s*([\d,]+\.?\d*)", txt)
    if m_s:
        try:
            out["state_grants"] = float(m_s.group(1).replace(",", ""))
        except ValueError:
            pass
    if grants_total is not None and out["central_grants"] is None and out["state_grants"] is None:
        # We have only the combined number
        pass
    return out


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------


def enrich_projects(projects_path: Path, ocr_path: Path, corp: str) -> None:
    """Enrich the existing projects JSON with additive fields."""
    if not projects_path.exists():
        raise FileNotFoundError(f"Projects file not found: {projects_path}")

    data = json.loads(projects_path.read_text(encoding="utf-8"))
    projects: list[dict] = data["projects"]

    pages = _load_ocr_pages(ocr_path)
    # Split English (pages 41+) vs Kannada (pages 1-38)
    eng_pages = {pn: t for pn, t in pages.items() if pn >= 41}
    kan_pages = {pn: t for pn, t in pages.items() if pn <= 38}

    sections = SECTIONS_BY_CORP.get(corp)
    if sections is None:
        raise SystemExit(f"No sections table defined for corp={corp!r}")

    # Pre-compute cross-goal links
    links = _link_goals(projects)

    # Add per-project fields (additive, don't overwrite existing)
    for p in projects:
        page = p.get("page")
        sec = _section_for_page(page, sections)
        # Section + Kannada section title
        if "section" not in p:
            p["section"] = sec["code"] if sec else None
        if "section_local" not in p:
            p["section_local"] = sec["title_local"] if sec else None
        # Free-text concatenation used for inference
        text_blob = " ".join(
            x for x in (p.get("name"), p.get("description"), p.get("verbatim_quote")) if x
        )
        # Beneficiary
        if "beneficiary_count" not in p:
            n, btype = _infer_beneficiary(text_blob)
            p["beneficiary_count"] = n
            p["beneficiary_type"] = btype
        # Location / Ward
        if "location" not in p:
            p["location"] = _infer_location(text_blob)
        if "ward" not in p:
            # Look for explicit ward mentions; otherwise null
            wm = re.search(r"\bWard\s+(?:No\.?\s*)?(\d{1,3})\b", text_blob, re.IGNORECASE)
            p["ward"] = wm.group(1) if wm else None
        # Timeline
        if "timeline" not in p:
            p["timeline"] = _infer_timeline(text_blob)
        # is_continuing
        if "is_continuing" not in p:
            p["is_continuing"] = _is_continuing(text_blob)
        # Linked goals
        if "linked_goal_ids" not in p:
            p["linked_goal_ids"] = links.get(p["id"], [])
        # Verbatim quote (Kannada)
        if "verbatim_quote_local" not in p:
            p["verbatim_quote_local"] = _kannada_for_quote(
                p.get("verbatim_quote"), kan_pages, page
            )

    # Top-level enrichments
    if pages:
        data["speech_total_pages"] = max(pages.keys())
    else:
        data["speech_total_pages"] = data.get("speech_total_pages")
    data["sections"] = [
        {**s, "intro_text": (eng_pages.get(s["page_start"], "")[:300]).strip() or None}
        for s in sections
    ]
    data["budget_at_a_glance"] = _extract_budget_at_a_glance(pages)
    intro, closing = _extract_intro_closing(eng_pages)
    data["commissioner_intro_text"] = intro
    data["commissioner_closing_text"] = closing

    projects_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("raw", "enrich"), default="raw",
                    help="raw=run OCR/extraction; enrich=update {corp}-projects.json")
    ap.add_argument("--pdf", help="Path to speech PDF (required for raw mode)")
    ap.add_argument("--out-dir", help="Output directory for raw text and projects.json")
    ap.add_argument("--kan-traineddata", default="/tmp/kan.traineddata",
                    help="Path to Kannada tessdata file (kan.traineddata)")
    ap.add_argument("--skip-ocr", action="store_true", help="Skip OCR step in raw mode")
    ap.add_argument("--corp", default="south", help="Corporation id for enrich mode")
    args = ap.parse_args()

    if args.mode == "raw":
        if not args.pdf or not args.out_dir:
            ap.error("--pdf and --out-dir are required for --mode raw")
        pdf = Path(args.pdf)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        plumber_info = extract_pdfplumber(pdf, out_dir / "raw-text.txt")
        pymu_info = extract_pymupdf(pdf, out_dir / "raw-text-fitz.txt")
        print("pdfplumber:", plumber_info)
        print("PyMuPDF   :", pymu_info)

        if not args.skip_ocr:
            kan = Path(args.kan_traineddata)
            if not kan.exists():
                raise FileNotFoundError(
                    f"Kannada traineddata not found at {kan}. Download from "
                    "https://github.com/tesseract-ocr/tessdata_best/raw/main/kan.traineddata"
                )
            workdir = out_dir / "ocr-tmp"
            pages, out = ocr_pages(pdf, workdir, kan)
            print(f"OCR: wrote {out} ({sum(len(t) for _, t in pages)} chars)")
            shutil.rmtree(workdir, ignore_errors=True)
        return

    # mode == enrich
    if args.corp not in SECTIONS_BY_CORP:
        raise SystemExit(
            f"--corp {args.corp!r} not supported. Known: {sorted(SECTIONS_BY_CORP)}"
        )
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent.parent / "data/bengaluru/budgets/2026-27/speech"
    projects_path = out_dir / f"{args.corp}-projects.json"
    ocr_path = out_dir / f"{args.corp}-raw-ocr.txt"
    enrich_projects(projects_path, ocr_path, args.corp)
    print(f"Enriched: {projects_path}")


if __name__ == "__main__":
    main()
