"""Dispatch uploads by sniffed content, and process every file."""

from src.faro_docs.ingest.pdf_ingest import ingest_pdf
from src.faro_docs.ingest.tabular import ingest_csv, ingest_excel
from src.faro_docs.model import Document

NOTE_UNSUPPORTED = (
    "Dateityp „{suffix}“ wird nicht unterstützt. Unterstützt werden derzeit "
    "PDF, Excel (XLSX/XLS), CSV und Textdateien."
)
NOTE_UNREADABLE = "Die Datei „{filename}“ konnte nicht gelesen werden: {error}"


def detect_kind(filename: str, raw: bytes) -> str:
    """Sniff content first; users rename files and extensions lie."""
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04") and b"xl/" in raw[:4096]:
        return "excel"
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel_legacy"

    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf":
        return "pdf"
    if suffix in {"xlsx", "xlsm"}:
        return "excel"
    if suffix == "xls":
        return "excel_legacy"
    if suffix in {"csv", "tsv"}:
        return "csv"
    if suffix in {"txt", "md"}:
        return "text"

    sample = raw[:2048]
    if sample and b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "csv" if any(d in sample for d in b";,\t") else "text"
        except UnicodeDecodeError:
            pass
    return "unknown"


def ingest_one(raw: bytes, filename: str, document_id: str) -> Document:
    kind = detect_kind(filename, raw)
    try:
        if kind == "pdf":
            return ingest_pdf(raw, document_id, filename)
        if kind == "excel":
            return ingest_excel(raw, document_id, filename)
        if kind == "excel_legacy":
            return ingest_excel(raw, document_id, filename, legacy=True)
        if kind == "csv":
            return ingest_csv(raw, document_id, filename)
        if kind == "text":
            from src.faro_docs.german import decode_text

            text, _ = decode_text(raw)
            return Document(
                id=document_id, filename=filename, media_type="text/plain", text=text
            )
    except Exception as error:  # degrade, never abort the whole upload
        return Document(
            id=document_id,
            filename=filename,
            media_type="",
            notes=[NOTE_UNREADABLE.format(filename=filename, error=error)],
        )

    suffix = filename.rsplit(".", 1)[-1] if "." in filename else "?"
    return Document(
        id=document_id,
        filename=filename,
        media_type="",
        notes=[NOTE_UNSUPPORTED.format(suffix=suffix)],
    )


def ingest_all(files: list[tuple[str, bytes]]) -> list[Document]:
    """Every uploaded file becomes its own Document with namespaced table ids."""
    return [
        ingest_one(raw, filename, document_id=f"dok{index}")
        for index, (filename, raw) in enumerate(files, start=1)
    ]
