"""Tools the model may call, and the loop that runs them."""

import json
import queue
import threading
import time
from collections.abc import Iterator

import pandas as pd

from src.faro_docs.facts import compute_facts
from src.faro_docs.messages_de import (
    DENKT_NACH,
    OLLAMA_NICHT_ERREICHBAR,
    ZU_VIELE_RUNDEN,
    provenance_footer,
)
from src.faro_docs.model import Document

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_documents",
        "description": "Alle hochgeladenen Dokumente mit Dateiname und Tabellenzahl auflisten.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "list_tables",
        "description": (
            "Alle erkannten Tabellen mit Id, Spalten und Zeilenzahl auflisten. "
            "IMMER zuerst aufrufen, wenn nach der Anzahl der Tabellen, Blätter "
            "oder Dokumente gefragt wird."
        ),
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "count_rows",
        "description": (
            "Zeilen einer Tabelle zählen. Mit table='alle' die Gesamtzahl über "
            "alle Tabellen und Dokumente hinweg."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Tabellen-Id oder 'alle'"},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "sum_column",
        "description": "Eine Zahlenspalte einer Tabelle summieren.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "get_row",
        "description": "Eine einzelne Zeile über ihren nullbasierten Index holen.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "index": {"type": "integer"},
        }, "required": ["table", "index"]},
    }},
    {"type": "function", "function": {
        "name": "find_rows",
        "description": "Zeilen suchen, deren Spalte einen Text enthält.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "contains": {"type": "string"},
        }, "required": ["table", "column", "contains"]},
    }},
]

SYSTEM_PROMPT = (
    "Du beantwortest Fragen zu hochgeladenen Dokumenten. Es kann sich um "
    "alles handeln: Rechnungen, Lieferscheine, Verträge, Berichte, Listen.\n\n"
    "Regeln:\n"
    "1. Zahlen, Anzahlen und Summen NIE selbst zählen oder addieren. Rufe das "
    "passende Werkzeug auf. Deine eigene Rechnung ist nicht verlässlich.\n"
    "2. Fragen nach der Anzahl der Tabellen, Blätter oder Dokumente IMMER mit "
    "list_tables beziehungsweise list_documents beantworten, nie schätzen.\n"
    "3. Eine Datei kann mehrere Tabellen enthalten, und es können mehrere "
    "Dateien hochgeladen sein. Prüfe das, bevor du eine Zahl nennst.\n"
    "4. Für Gesamtzahlen über alles hinweg count_rows mit table='alle' nutzen "
    "oder den Wert aus FAKTEN._zusammenfassung übernehmen.\n"
    "5. Inhaltliche Fragen (Worum geht es? Wer ist der Absender? Was steht in "
    "Abschnitt 4?) direkt aus dem Dokumenttext beantworten.\n"
    "6. Steht die Antwort nicht im Dokument, sage genau das (in der Sprache "
    "der Frage, z. B. „Das steht nicht im Dokument.“ auf Deutsch oder "
    "„That is not in the document.“ auf Englisch). Nichts erfinden.\n"
    "7. Antworte in der Sprache, in der die Frage gestellt wurde -- Deutsch "
    "bei einer deutschen Frage, Englisch bei einer englischen Frage, "
    "ebenso in jeder anderen Sprache. Nicht die Sprache des Dokuments "
    "annehmen, wenn die Frage in einer anderen Sprache gestellt wurde. "
    "In ganzen Sätzen, knapp."
)


def _all_tables(documents: list[Document]) -> dict:
    return {table.id: table for document in documents for table in document.tables}


def run_tool(name: str, args: dict, documents: list[Document]):
    if name == "list_documents":
        return {
            document.id: {
                "dateiname": document.filename,
                "tabellen": [table.id for table in document.tables],
            }
            for document in documents
        }

    tables = _all_tables(documents)

    if name == "list_tables":
        return {
            table_id: {
                "bezeichnung": table.label,
                "spalten": [str(c) for c in table.frame.columns],
                "zeilen": table.row_count(),
            }
            for table_id, table in tables.items()
        }

    if name == "count_rows" and args.get("table") == "alle":
        return sum(table.row_count() for table in tables.values())

    table_id = args.get("table")
    if table_id not in tables:
        raise ValueError(
            f"Unbekannte Tabelle „{table_id}“. Gültige Tabellen: {list(tables)}"
        )
    table = tables[table_id]

    if name == "count_rows":
        return table.row_count()

    if name == "sum_column":
        column = args["column"]
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        numeric = pd.to_numeric(table.frame[column], errors="coerce")
        if len(table.frame) and numeric.notna().sum() == 0:
            raise ValueError(f"Spalte „{column}“ enthält keine Zahlen zum Summieren.")
        return float(numeric.sum())

    if name == "get_row":
        return table.frame.iloc[int(args["index"])].to_dict()

    if name == "find_rows":
        column = args["column"]
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        needle = str(args["contains"]).lower()
        mask = table.frame[column].astype(str).str.lower().str.contains(needle, na=False)
        return table.frame[mask].head(50).to_dict(orient="records")

    raise ValueError(f"Unbekanntes Werkzeug: {name}")


def build_context(documents: list[Document], max_text_chars: int = 40000) -> str:
    """Document text, table markdown, and code-computed facts."""
    parts = []
    for document in documents:
        text = document.text or ""
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "\n…[Text gekürzt]"
        parts.append(f"## Dokument {document.id}: {document.filename}\n{text}")
        for table in document.tables:
            parts.append(
                f"### {table.id} — {table.label} "
                f"(Spalten: {', '.join(str(c) for c in table.frame.columns)})\n"
                + table.frame.head(200).to_markdown(index=False)
            )
    parts.append(
        "FAKTEN: " + json.dumps(compute_facts(documents), ensure_ascii=False, default=str)
    )
    return "\n\n".join(parts)


_HEARTBEAT = object()
_DONE = object()


def _stream_with_heartbeat(client, model, messages, tools, interval=3.0):
    """Ollama prefills a long document before emitting anything; a silent gap
    that long drops the browser connection, so emit a heartbeat while waiting."""
    channel: queue.Queue = queue.Queue()

    def worker():
        try:
            for chunk in client.chat(
                model=model, messages=messages, tools=tools, stream=True
            ):
                channel.put(chunk)
        except Exception as error:
            channel.put(error)
        finally:
            channel.put(_DONE)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        try:
            item = channel.get(timeout=interval)
        except queue.Empty:
            yield _HEARTBEAT
            continue
        if item is _DONE:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def answer(
    documents: list[Document],
    question: str,
    model: str,
    host: str,
    max_rounds: int = 6,
    max_text_chars: int = 40000,
) -> Iterator[str]:
    """Plain sync generator -- async pipes never signal completion (open-webui#20196)."""
    import ollama

    client = ollama.Client(host=host)
    context = build_context(documents, max_text_chars=max_text_chars)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOKUMENTE:\n{context}\n\nFRAGE: {question}"},
    ]
    tools = TOOL_SCHEMAS if _all_tables(documents) else None
    used_tools = False

    for _ in range(max_rounds):
        content = ""
        tool_calls = None
        started = time.monotonic()
        seen_output = False
        try:
            for item in _stream_with_heartbeat(client, model, messages, tools):
                if item is _HEARTBEAT:
                    if not seen_output:
                        yield DENKT_NACH.format(
                            sekunden=int(time.monotonic() - started)
                        ) + " "
                    continue
                seen_output = True
                piece = item.get("message", {}).get("content", "")
                if piece:
                    content += piece
                    yield piece
                if item.get("message", {}).get("tool_calls"):
                    tool_calls = item["message"]["tool_calls"]
        except Exception as error:
            yield "\n\n" + OLLAMA_NICHT_ERREICHBAR.format(host=host)
            yield f"\n\n_({error})_"
            return

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )
        if not tool_calls:
            sources = {"tabelle"} if used_tools else {"text"}
            notes = [n for d in documents for n in d.notes]
            notes += [n for d in documents for t in d.tables for n in t.notes]
            yield provenance_footer(sources, notes)
            return

        used_tools = True
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            try:
                result = run_tool(name, args, documents)
            except Exception as error:
                result = {"fehler": str(error)}
            yield f"\n_- `{name}({args})` → `{result}`_\n"
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                    "tool_name": name,
                }
            )

    yield "\n\n" + ZU_VIELE_RUNDEN
