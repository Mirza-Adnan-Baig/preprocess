"""PDF ingest. Keeps every distinct table and merges ones split across pages."""

from src.faro_docs.model import Document
from src.faro_docs.tables import build_table

NOTE_SCAN_SUSPECTED = (
    "Diese Datei enthält kaum auslesbaren Text und ist vermutlich ein Scan. "
    "Texterkennung ist in dieser Version noch nicht aktiv."
)
_MIN_TEXT_PER_PAGE = 20


def _normalise_header_cell(cell: str) -> str:
    """Collapse the cosmetic differences between the same header re-printed
    on a later page: line breaks, repeated spaces, capitalisation."""
    return " ".join(str(cell).split()).casefold()


def ingest_pdf(raw: bytes, document_id: str, filename: str) -> Document:
    import fitz  # PyMuPDF

    document = fitz.open(stream=raw, filetype="pdf")
    try:
        page_texts: list[str] = []
        groups: dict[tuple, list[list]] = {}

        originals: dict[tuple, list] = {}
        for page in document:
            page_texts.append(page.get_text())
            for found in page.find_tables().tables:
                rows = found.extract()
                if len(rows) < 2:
                    continue
                header = [
                    "" if cell is None else str(cell).strip() for cell in rows[0]
                ]
                # Group by a normalised key, not the raw header. The same
                # table continued on page 2 of a 41-page catalogue routinely
                # re-prints its header with a line break, double space or
                # different capitalisation; keying on the raw text split one
                # logical table into a pile of near-duplicate ones, each with
                # a fraction of the rows.
                key = tuple(_normalise_header_cell(cell) for cell in header)
                groups.setdefault(key, []).extend(rows[1:])
                originals.setdefault(key, header)

        text = "\n\n".join(page_texts)
        notes: list[str] = []
        if len(text.strip()) < _MIN_TEXT_PER_PAGE * max(1, len(document)):
            notes.append(NOTE_SCAN_SUSPECTED)

        tables = []
        ordered = sorted(groups.items(), key=lambda item: -len(item[1]))
        for index, (key, body) in enumerate(ordered, start=1):
            table = build_table(
                [list(originals.get(key, key))] + body,
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
            page_count=len(document),
        )
    finally:
        document.close()
