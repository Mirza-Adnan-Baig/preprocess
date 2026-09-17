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
