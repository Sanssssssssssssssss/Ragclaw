from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    """Return one CLI parser from no inputs and define the supported pdftotext-compatible arguments."""

    parser = argparse.ArgumentParser(
        description="Extract text from a PDF file into a text file with a minimal pdftotext-compatible interface."
    )
    parser.add_argument("-layout", action="store_true", help="Accepted for compatibility; layout preservation is best-effort.")
    parser.add_argument("-f", type=int, default=1, help="First page to extract, 1-based.")
    parser.add_argument("-l", type=int, help="Last page to extract, 1-based.")
    parser.add_argument("input_pdf", help="Input PDF path.")
    parser.add_argument("output_txt", help="Output text path.")
    return parser


def _load_pdf_reader(pdf_path: Path):
    """Return one PDF reader instance from a file path input and support either pypdf or PyPDF2."""

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore

    return PdfReader(str(pdf_path))


def _extract_page_text(page) -> str:
    """Return one text block from a PDF page input and normalize empty extraction results."""

    text = page.extract_text() or ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def main() -> int:
    """Return one exit code from CLI inputs and extract PDF text into the requested output file."""

    args = _build_parser().parse_args()
    input_pdf = Path(args.input_pdf)
    output_txt = Path(args.output_txt)

    if not input_pdf.exists():
        print(f"Input PDF not found: {input_pdf}", file=sys.stderr)
        return 1

    reader = _load_pdf_reader(input_pdf)
    total_pages = len(reader.pages)
    first_page = max(1, args.f)
    last_page = min(args.l or total_pages, total_pages)

    if first_page > last_page:
        print("Invalid page range for PDF extraction.", file=sys.stderr)
        return 1

    page_texts: list[str] = []
    for page_index in range(first_page - 1, last_page):
        page = reader.pages[page_index]
        page_texts.append(_extract_page_text(page).strip())

    output_txt.parent.mkdir(parents=True, exist_ok=True)
    output_txt.write_text("\n\n".join(page_texts).strip() + "\n", encoding="utf-8")
    print(
        f"Extracted {last_page - first_page + 1} page(s) from {input_pdf} to {output_txt}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
