import random

import pandas as pd
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

ARTICLE_NAMES = [
    "iPhone 13 Display", "Samsung S22 Battery", "USB-C Charging Port",
    "iPhone Back Glass", "Pixel 7 Camera Module", "Phone Case Clear",
    "Screen Protector Tempered", "Lightning Cable 1m", "Wireless Charger Pad",
    "Replacement SIM Tray",
]


def generate_messy_inventory_xlsx(path: str, n_rows: int, seed: int = 0) -> None:
    rng = random.Random(seed)
    wb = Workbook()
    ws = wb.active

    ws.append(["FARO Inventory Export"])
    ws.append([])
    ws.append(["Article", "Quantity", "Unit Price EUR", "Supplier"])

    for i in range(n_rows):
        row = [
            rng.choice(ARTICLE_NAMES),
            rng.randint(1, 200),
            round(rng.uniform(2.5, 150.0), 2),
            f"Supplier {rng.randint(1, 5)}",
        ]
        if rng.random() < 0.05:
            blank_col = rng.randint(0, 3)
            row[blank_col] = None
        ws.append(row)

    wb.save(path)


def generate_invoice_pdf(path: str, n_line_items: int, seed: int = 0) -> None:
    rng = random.Random(seed)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    elements = [
        Paragraph("FARO Import-Export GmbH", styles["Title"]),
        Paragraph(f"Invoice #{rng.randint(10000, 99999)}", styles["Normal"]),
        Spacer(1, 12),
    ]

    data = [["Article", "Qty", "Unit Price (EUR)"]]
    for _ in range(n_line_items):
        data.append([
            rng.choice(ARTICLE_NAMES),
            str(rng.randint(1, 100)),
            f"{rng.uniform(2.5, 150.0):.2f}",
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Payment due within 30 days. Thank you for your business.", styles["Normal"]))

    doc.build(elements)
