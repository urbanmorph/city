#!/usr/bin/env python3
"""Extract text from a Bengaluru City Corporation Budget Speech PDF.

The BSCC 2026-27 speech is bilingual (Kannada pages 1-38, English pages
41-71, budget-table pages 39-40 and 72-73). Both ``pdfplumber`` and
``PyMuPDF`` fail on Kannada because the PDF uses font-remapped CID glyphs
(pdfplumber emits ``(cid:NNNN)`` tokens; PyMuPDF emits mojibake Unicode).
OCR with Tesseract (``kan+eng``) is the most reliable path and also picks
up the English section cleanly.

This script:
1. Extracts text with pdfplumber and PyMuPDF (for audit / diff).
2. Renders each page to 300 DPI PNG and OCRs with Tesseract ``kan+eng``.
3. Writes three raw-text files into the output directory for review.

Prerequisites:
    pip install pdfplumber pymupdf
    brew install tesseract
    # Kannada traineddata (not in Homebrew):
    curl -L https://github.com/tesseract-ocr/tessdata_best/raw/main/kan.traineddata \\
        -o /tmp/kan.traineddata

Usage:
    python3 extract_budget_speech.py \\
        --pdf "supporting-docs/budget-pdfs/Budget Speech - BSCC (Final).pdf" \\
        --out-dir data/bengaluru/budgets/2026-27/speech \\
        --kan-traineddata /tmp/kan.traineddata
"""

from __future__ import annotations

import argparse
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
        if not Path(str(txt_stem) + ".txt").exists():
            r = subprocess.run(
                ["tesseract", str(img), str(txt_stem), "-l", "kan+eng", "--psm", "6"],
                capture_output=True, env=env,
            )
            if r.returncode != 0:
                raise RuntimeError(f"tesseract failed on page {pn}: {r.stderr!r}")
        with open(str(txt_stem) + ".txt", "r", encoding="utf-8") as fh:
            pages.append((pn, fh.read()))
        if pn % 10 == 0:
            print(f"  OCR {pn}/{doc.page_count} ({time.time()-t0:.1f}s)")
    doc.close()

    out = workdir.parent / "raw-ocr.txt"
    with out.open("w", encoding="utf-8") as fh:
        for pn, txt in pages:
            fh.write(f"===== PAGE {pn} =====\n{txt}\n\n")
    return pages, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--kan-traineddata", default="/tmp/kan.traineddata",
                    help="Path to Kannada tessdata file (kan.traineddata)")
    ap.add_argument("--skip-ocr", action="store_true", help="Skip OCR step")
    args = ap.parse_args()

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
        # Clean up per-page intermediates; keep the combined file.
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
