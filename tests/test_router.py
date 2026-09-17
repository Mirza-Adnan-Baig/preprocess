from src.extractors.router import extract_document
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx, generate_invoice_pdf


def test_router_handles_xlsx(tmp_path):
    path = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(path), n_rows=10, seed=5)

    result = extract_document(str(path))

    assert result.kind == "tabular"
    assert result.parse_failed is False
    assert result.facts["row_count"] == 10


def test_router_handles_pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=10, seed=5)

    result = extract_document(str(path))

    assert result.kind == "pdf"
    assert result.parse_failed is False
    assert "FARO" in result.markdown


def test_router_reports_unparseable_file(tmp_path):
    path = tmp_path / "mystery.xyz"
    path.write_bytes(b"\x00\x01\x02 not a real document")

    result = extract_document(str(path))

    assert result.parse_failed is True
    assert result.message is not None
    assert "couldn't" in result.message.lower() or "could not" in result.message.lower()


def test_router_pdf_populates_dataframe_from_table(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=12, seed=6)

    result = extract_document(str(path))

    assert result.kind == "pdf"
    assert result.dataframe is not None
    assert len(result.dataframe) == 12
    assert "Article" in result.dataframe.columns


def test_router_pdf_dataframe_spans_multiple_pages(tmp_path):
    path = tmp_path / "invoice.pdf"
    n_line_items = 80
    generate_invoice_pdf(str(path), n_line_items=n_line_items, seed=9)

    result = extract_document(str(path))

    # sanity check: this fixture must actually span multiple PDF pages,
    # otherwise this test wouldn't exercise the cross-page merge at all
    from src.extractors.pdf import extract_pdf
    assert len(extract_pdf(str(path)).pages) >= 2

    assert result.kind == "pdf"
    assert result.dataframe is not None
    assert len(result.dataframe) == n_line_items
    assert "Article" in result.dataframe.columns
