"""Show exactly what the assistant sees in a document -- no model involved.

The point: when an answer at work looks wrong, this tells you within
seconds whether the problem is the *extraction* (wrong tables, missing
rows, a barcode column turned into numbers) or the *model* (extraction
fine, model picked the wrong tool). Those need completely different
fixes, and guessing between them wastes a lot of time.

    python -m tools.inspect_document "Katalog.pdf"
    python -m tools.inspect_document "Katalog.pdf" --frage "Zubehör"

With --frage it also runs the deterministic searches for that term, so
you can see the true counts before ever asking the model.
"""

import argparse
import pathlib
import sys

from src.faro_docs.answer import run_tool
from src.faro_docs.ingest.router import ingest_all


def _line(char: str = "-", width: int = 72) -> str:
    return char * width


def describe(path: pathlib.Path, term: str | None = None) -> None:
    raw = path.read_bytes()
    documents = ingest_all([(path.name, raw)])

    for document in documents:
        print(_line("="))
        print(f"DATEI      {document.filename}")
        print(f"Typ        {document.media_type}")
        if document.page_count:
            print(f"Seiten     {document.page_count}")
        print(f"Textlänge  {len(document.text):,} Zeichen".replace(",", "."))
        print(f"Tabellen   {len(document.tables)}")
        for note in document.notes:
            print(f"HINWEIS    {note}")

        if not document.tables:
            print("\n!! Keine Tabelle erkannt.")
            print("   Zahlenfragen sind dann nur über den Text beantwortbar.")
            print("   Bei einem gescannten PDF ist das zu erwarten -- ohne")
            print("   Texterkennung kommt hier nichts Brauchbares heraus.")

        for table in document.tables:
            print(_line())
            print(f"{table.id}  ({table.label})")
            print(f"  Zeilen: {table.row_count()}")
            print("  Spalten:")
            for name in table.frame.columns:
                info = table.columns.get(str(name))
                style = info.numeric_style if info else "?"
                meaning = {
                    "german": "Zahl (deutsches Format 1.234,56)",
                    "english": "Zahl (englisches Format 1,234.56)",
                    "integer": "ganze Zahl",
                    "none": "Text / Kennnummer (wird NICHT gerechnet)",
                }.get(style, style)
                sample = [
                    str(v) for v in table.frame[name].head(2).tolist()
                ]
                print(f"    - {name:<28} {meaning}")
                print(f"      Beispiel: {', '.join(sample) if sample else '(leer)'}")
            for note in table.notes:
                print(f"  HINWEIS: {note}")
            if table.totals_rows:
                print(f"  {len(table.totals_rows)} Summenzeile(n) ausgeschlossen, "
                      "damit nicht doppelt gezählt wird")

        if term:
            print(_line())
            print(f'SUCHE NACH "{term}" — das sind die wahren Zahlen,')
            print("gegen die du die Antwort des Modells prüfen kannst:")
            hits = run_tool(
                "count_text_occurrences",
                {"search": term, "document": document.id},
                documents,
            )
            print(f"  im Fließtext:            {hits} Treffer")
            for table in document.tables:
                for name in table.frame.columns:
                    try:
                        count = run_tool(
                            "count_matching_rows",
                            {"table": table.id, "column": str(name), "contains": term},
                            documents,
                        )
                    except ValueError:
                        continue
                    if count:
                        print(f"  {table.id} / Spalte „{name}“: {count} Zeilen")

    print(_line("="))
    print("Wenn hier alles richtig aussieht, liegt ein falsche Antwort am")
    print("Modell (falsches Werkzeug gewählt) -- schau dann im Chat auf die")
    print("Werkzeug-Zeile unter der Antwort. Sieht es hier schon falsch aus,")
    print("liegt es an der Extraktion.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zeigt, was der Assistent aus einem Dokument herausliest."
    )
    parser.add_argument("datei", help="PDF, CSV, Excel oder Textdatei")
    parser.add_argument(
        "--frage", "--term", dest="term", default=None,
        help="Optional: Begriff, dessen echte Trefferzahl ausgegeben wird",
    )
    args = parser.parse_args()

    path = pathlib.Path(args.datei)
    if not path.is_file():
        print(f"Datei nicht gefunden: {path}", file=sys.stderr)
        raise SystemExit(1)
    describe(path, args.term)


if __name__ == "__main__":
    main()
