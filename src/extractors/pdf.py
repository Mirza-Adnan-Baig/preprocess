import io
from dataclasses import dataclass

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

OCR_TEXT_THRESHOLD = 20  # chars; below this, treat page as image-only


@dataclass
class PDFPage:
    page_number: int
    text: str
    used_ocr: bool
    tables_markdown: list[str]


@dataclass
class PDFExtraction:
    pages: list[PDFPage]
    full_text: str


def _table_to_markdown(table) -> str:
    rows = table.extract()
    if not rows:
        return ""
    header, *body = rows
    header_line = "| " + " | ".join(str(c or "") for c in header) + " |"
    sep_line = "| " + " | ".join("---" for _ in header) + " |"
    body_lines = [
        "| " + " | ".join(str(c or "") for c in row) + " |"
        for row in body
    ]
    return "\n".join([header_line, sep_line, *body_lines])


def _ocr_page(page) -> str:
    pix = page.get_pixmap(dpi=200)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(image, lang="deu+eng")


def extract_pdf(path: str) -> PDFExtraction:
    doc = fitz.open(path)
    try:
        pages: list[PDFPage] = []

        for i, page in enumerate(doc):
            text = page.get_text()
            used_ocr = False
            if len(text.strip()) < OCR_TEXT_THRESHOLD:
                text = _ocr_page(page)
                used_ocr = True

            found = page.find_tables()
            tables_markdown = [_table_to_markdown(t) for t in found.tables]

            pages.append(PDFPage(
                page_number=i + 1,
                text=text,
                used_ocr=used_ocr,
                tables_markdown=tables_markdown,
            ))

        full_text = "\n\n".join(p.text for p in pages)
        return PDFExtraction(pages=pages, full_text=full_text)
    finally:
        doc.close()
