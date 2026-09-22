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


def test_page_count_is_recorded(tmp_path):
    """'Wie viele Seiten hat das Dokument?' is a plain, common question
    and the answer was simply thrown away before."""
    body = [[f"Artikel {i}", str(i)] for i in range(1, 120)]
    path = _build_pdf(tmp_path / "viele.pdf", [("FARO GmbH", ["Artikel", "Menge"], body)])
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="viele.pdf")
    assert doc.page_count >= 2


def test_cosmetic_header_differences_between_pages_still_merge(tmp_path):
    """The same table continued on a later page routinely re-prints its
    header with different spacing or capitalisation. Keying the merge on
    the raw header text split one logical table into a pile of
    near-duplicates, each holding a fraction of the rows -- so a count on
    a 41-page catalogue would report only the rows of whichever fragment
    the model happened to pick."""
    first = [[f"Artikel {i}", str(i)] for i in range(1, 30)]
    second = [[f"Artikel {i}", str(i)] for i in range(30, 60)]
    path = _build_pdf(
        tmp_path / "kopf.pdf",
        [
            ("Katalog", ["Barcode-EAN", "Menge"], first),
            ("Katalog", ["BARCODE-EAN ", " Menge"], second),
        ],
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="kopf.pdf")
    assert len(doc.tables) == 1, "cosmetic header differences must not split the table"
    assert doc.tables[0].row_count() == 59


def test_faro_style_invoice_end_to_end(tmp_path):
    """The real invoice shape: article number, description, barcode/EAN,
    unit price in German format, quantity, line total."""
    from src.faro_docs.answer import run_tool

    body = [
        ["33447", "Akku mit TI-IC Chip fuer Apple iPhone 7 Plus",
         "4051805334476", "4,86", "1", "4,86"],
        ["32965", "LCD + Touch fuer Apple iPhone 7 Plus AAA+",
         "4051805329656", "10,93", "1", "10,93"],
    ]
    path = _build_pdf(
        tmp_path / "rechnung.pdf",
        [("RECHNUNG 26-126920",
          ["Artikelnr.", "Bezeichnung", "Barcode-EAN", "Einzelpreis", "Menge", "Gesamtpreis"],
          body)],
    )
    doc = ingest_pdf(path.read_bytes(), document_id="dok1", filename="rechnung.pdf")
    table = doc.tables[0]

    assert table.row_count() == 2
    # the barcode column must survive as exact text, not become a float
    assert "4051805334476" in table.frame["Barcode-EAN"].astype(str).tolist()
    # and the money adds up through the normal tool path
    total = run_tool(
        "sum_column", {"table": table.id, "column": "Gesamtpreis"}, [doc]
    )
    assert total["summe"] == pytest.approx(15.79)
