import pytest

from src.faro_docs.ingest.pdf_ingest import ingest_pdf

reportlab = pytest.importorskip("reportlab")


def _build_pdf(path, sections):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    elements = []
    for index, (title, header, body) in enumerate(sections):
        if index:
            elements.append(PageBreak())
        elements.append(Paragraph(title, styles["Title"]))
        table = Table([header] + body, repeatRows=1)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        elements.append(table)
    SimpleDocTemplate(str(path), pagesize=A4).build(elements)
    return path


def test_keeps_two_different_tables_separate(tmp_path):
    path = _build_pdf(
        tmp_path / "zwei.pdf",
        [
            ("FARO GmbH", ["Artikel", "Menge"], [["A", "1"], ["B", "2"]]),
            ("Lagerhaus Müller", ["Position", "Anzahl"], [["C", "3"]]),
        ],
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="zwei.pdf")
    assert len(doc.tables) == 2
    assert doc.total_rows() == 3


def test_merges_one_table_split_across_pages(tmp_path):
    body = [[f"Artikel {i}", str(i)] for i in range(1, 80)]
    path = _build_pdf(tmp_path / "lang.pdf", [("FARO GmbH", ["Artikel", "Menge"], body)])
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="lang.pdf")
    assert len(doc.tables) == 1
    assert doc.tables[0].row_count() == 79


def test_text_is_always_available(tmp_path):
    path = _build_pdf(tmp_path / "t.pdf", [("FARO GmbH", ["Artikel"], [["A"]])])
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="t.pdf")
    assert "FARO" in doc.text


def test_pdf_without_tables_still_returns_text(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    path = tmp_path / "brief.pdf"
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sehr geehrte Damen und Herren", getSampleStyleSheet()["Normal"])]
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="brief.pdf")
    assert doc.tables == []
    assert "Sehr geehrte" in doc.text
