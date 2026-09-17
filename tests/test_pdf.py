from src.extractors.pdf import extract_pdf
from scripts.generate_synthetic_data import generate_invoice_pdf


def test_extract_pdf_reads_text_without_ocr(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=25, seed=4)

    result = extract_pdf(str(path))

    assert len(result.pages) >= 1
    assert result.pages[0].used_ocr is False
    assert "FARO Import-Export" in result.full_text


def test_extract_pdf_finds_table(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=25, seed=4)

    result = extract_pdf(str(path))

    all_tables = [t for p in result.pages for t in p.tables_markdown]
    assert len(all_tables) >= 1
    assert "Article" in all_tables[0]


def test_extract_pdf_tables_raw_matches_markdown_count(tmp_path):
    path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(path), n_line_items=25, seed=4)

    result = extract_pdf(str(path))

    for page in result.pages:
        assert len(page.tables_raw) == len(page.tables_markdown)
    total_raw_tables = sum(len(p.tables_raw) for p in result.pages)
    assert total_raw_tables >= 1
    # the invoice's line-item table has a header row + 25 data rows
    biggest = max((t for p in result.pages for t in p.tables_raw), key=len)
    assert len(biggest) == 26
