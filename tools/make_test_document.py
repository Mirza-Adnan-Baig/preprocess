"""Build a realistic test catalogue -- and print the correct answers.

You can't take the real 41-page catalogue home, which makes testing
anywhere but at work impossible. This generates a document with the same
shape (article number, description, barcode, EAN, German prices,
quantity; no row-number column) at whatever length you want, as PDF, CSV
and Excel, and prints the true answers to the questions you'd ask it.

    python -m tools.make_test_document --seiten 41
    python -m tools.make_test_document --zeilen 1000 --format csv

Then ask the assistant the questions it printed and compare. Every
number it prints is computed from the data, not guessed.
"""

import argparse
import pathlib
import random

_PARTS = [
    "Akku mit TI-IC Chip für Apple iPhone {m}",
    "LCD + Touch für Apple iPhone {m} AAA+ schwarz",
    "Zubehör Ladekabel USB-C {m} cm",
    "Zubehör Schutzglas für iPhone {m}",
    "Kameraglas für Apple iPhone {m}",
    "Akkudeckel für Samsung Galaxy S{m}",
    "Displayeinheit Samsung Galaxy A{m} Service Pack",
    "Zubehör Werkzeugset {m}-teilig",
    "Ladebuchse Flexkabel iPhone {m}",
    "Hörmuschel Lautsprecher iPhone {m}",
]


def build_rows(count: int, seed: int = 20260923) -> list[list[str]]:
    generator = random.Random(seed)
    rows = []
    for index in range(count):
        template = _PARTS[index % len(_PARTS)]
        model_number = 7 + (index % 9)
        article = str(33000 + index)
        # EAN-13: deliberately includes codes starting with 0, and a few
        # repeated codes, because both occur in real exports and both have
        # broken this pipeline before.
        if index % 37 == 0 and index:
            ean = rows[index - 1][3]  # a genuine duplicate
        elif index % 11 == 0:
            ean = f"0{generator.randrange(10**11, 10**12 - 1)}"
        else:
            ean = str(generator.randrange(4 * 10**12, 5 * 10**12))
        barcode = "" if index % 53 == 0 and index else str(
            generator.randrange(10**12, 10**13 - 1)
        )
        euros = generator.randrange(1, 60)
        cents = generator.randrange(0, 100)
        price = f"{euros},{cents:02d}"
        quantity = str(generator.choice([1, 1, 1, 2, 3, 5, 10]))
        rows.append([
            article,
            template.format(m=model_number),
            barcode,
            ean,
            price,
            quantity,
        ])
    return rows


HEADER = ["Artikelnr.", "Bezeichnung", "Barcode", "EAN", "Einzelpreis", "Menge"]


def _german_float(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def print_answers(rows: list[list[str]]) -> None:
    prices = [_german_float(row[4]) for row in rows]
    quantities = [int(row[5]) for row in rows]
    zubehör = [row for row in rows if "Zubehör" in row[1]]
    zubehör_total = sum(_german_float(row[4]) for row in zubehör)
    missing_barcode = sum(1 for row in rows if not row[2])
    leading_zero = sum(1 for row in rows if row[3].startswith("0"))
    eans = [row[3] for row in rows]
    duplicate_eans = len({e for e in eans if eans.count(e) > 1})
    most_expensive = max(rows, key=lambda row: _german_float(row[4]))

    print("=" * 72)
    print("FRAGEN UND DIE RICHTIGEN ANTWORTEN")
    print("(die Zahlen hier sind gerechnet, nicht geschätzt)")
    print("=" * 72)
    print(f"  Wie viele Positionen hat die Liste?           {len(rows)}")
    print(f"  Wie viele Zubehör-Artikel gibt es?           {len(zubehör)}")
    print(f"  Was kosten alle Zubehör-Artikel zusammen?    "
          f"{zubehör_total:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    print(f"  Was ist die Summe aller Einzelpreise?         "
          f"{sum(prices):,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    print(f"  Wie viele Stück insgesamt (Menge summiert)?   {sum(quantities)}")
    print(f"  Wie viele Artikel kosten mehr als 30 Euro?    "
          f"{sum(1 for p in prices if p > 30)}")
    print(f"  Was ist der teuerste Artikel?                 "
          f"{most_expensive[1]} ({most_expensive[4]} EUR)")
    print(f"  Wie viele Zeilen haben keinen Barcode?        {missing_barcode}")
    print(f"  Wie viele EANs beginnen mit einer 0?          {leading_zero}")
    print(f"  Wie viele EANs kommen doppelt vor?            {duplicate_eans}")
    print(f"  Wie viele verschiedene Artikelnummern?        {len({r[0] for r in rows})}")
    print("=" * 72)


def write_csv(path: pathlib.Path, rows: list[list[str]]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(HEADER)
        writer.writerows(rows)


def write_excel(path: pathlib.Path, rows: list[list[str]]) -> None:
    import pandas as pd

    pd.DataFrame(rows, columns=HEADER).to_excel(path, index=False)


def write_pdf(path: pathlib.Path, rows: list[list[str]]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    styles = getSampleStyleSheet()
    body = [HEADER] + rows
    table = Table(body, repeatRows=1, colWidths=[55, 175, 85, 85, 55, 35])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    SimpleDocTemplate(str(path), pagesize=A4).build([
        Paragraph("faro IMPORT EXPORT GmbH &amp; Co. KG — Artikelliste", styles["Title"]),
        Paragraph("Testdatei, keine echten Preise.", styles["Normal"]),
        table,
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Realistische Testdatei bauen.")
    parser.add_argument("--zeilen", type=int, default=None, help="Anzahl Artikelzeilen")
    parser.add_argument("--seiten", type=int, default=None,
                        help="Ungefähre Seitenzahl im PDF (ca. 45 Zeilen je Seite)")
    parser.add_argument("--format", choices=["pdf", "csv", "xlsx", "alle"], default="alle")
    parser.add_argument("--ziel", default=".", help="Zielordner")
    args = parser.parse_args()

    count = args.zeilen or (args.seiten * 45 if args.seiten else 450)
    rows = build_rows(count)
    target = pathlib.Path(args.ziel)
    target.mkdir(parents=True, exist_ok=True)

    written = []
    if args.format in {"csv", "alle"}:
        path = target / "test_artikelliste.csv"
        write_csv(path, rows)
        written.append(path)
    if args.format in {"xlsx", "alle"}:
        path = target / "test_artikelliste.xlsx"
        write_excel(path, rows)
        written.append(path)
    if args.format in {"pdf", "alle"}:
        path = target / "test_artikelliste.pdf"
        try:
            write_pdf(path, rows)
            written.append(path)
        except ImportError:
            print("reportlab fehlt -- PDF übersprungen (CSV/Excel wurden gebaut).")

    print(f"{count} Zeilen erzeugt.")
    for path in written:
        print(f"  geschrieben: {path}")
    print()
    print_answers(rows)


if __name__ == "__main__":
    main()
