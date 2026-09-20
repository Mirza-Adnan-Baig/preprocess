"""PDF ingest. Keeps every distinct table and merges ones split across pages."""

from src.faro_docs.model import Document
from src.faro_docs.tables import build_table

NOTE_SCAN_SUSPECTED = (
    "Diese Datei enthält kaum auslesbaren Text und ist vermutlich ein Scan. "
    "Texterkennung ist in dieser Version noch nicht aktiv."
)
_MIN_TEXT_PER_PAGE = 20


def ingest_pdf(raw: bytes, document_id: str, filename: str) -> Document:
    import fitz  # PyMuPDF

    document = fitz.open(stream=raw, filetype="pdf")
    try:
        page_texts: list[str] = []
        groups: dict[tuple, list[list]] = {}

        for page in document:
            page_texts.append(page.get_text())
            for found in page.find_tables().tables:
                rows = found.extract()
                if len(rows) < 2:
                    continue
                header = tuple(
                    "" if cell is None else str(cell).strip() for cell in rows[0]
                )
                groups.setdefault(header, []).extend(rows[1:])

        text = "\n\n".join(page_texts)
        notes: list[str] = []
        if len(text.strip()) < _MIN_TEXT_PER_PAGE * max(1, len(document)):
            notes.append(NOTE_SCAN_SUSPECTED)

        tables = []
        ordered = sorted(groups.items(), key=lambda item: -len(item[1]))
        for index, (header, body) in enumerate(ordered, start=1):
            table = build_table(
                [list(header)] + body,
                table_id=f"{document_id}:tabelle{index}",
                label=f"Tabelle {index}",
            )
            if table.row_count():
                tables.append(table)

        return Document(
            id=document_id,
            filename=filename,
            media_type="application/pdf",
            text=text,
            tables=tables,
            notes=notes,
        )
    finally:
        document.close()
