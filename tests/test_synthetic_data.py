import pandas as pd
from scripts.generate_synthetic_data import generate_messy_inventory_xlsx, generate_invoice_pdf


def test_generate_messy_inventory_xlsx_row_count(tmp_path):
    out = tmp_path / "inventory.xlsx"
    generate_messy_inventory_xlsx(str(out), n_rows=50, seed=1)

    raw = pd.read_excel(out, header=None)
    # title row + blank row + header row + 50 data rows
    assert len(raw) == 53
    assert out.exists()


def test_generate_invoice_pdf_creates_file(tmp_path):
    out = tmp_path / "invoice.pdf"
    generate_invoice_pdf(str(out), n_line_items=30, seed=1)

    assert out.exists()
    assert out.stat().st_size > 0
