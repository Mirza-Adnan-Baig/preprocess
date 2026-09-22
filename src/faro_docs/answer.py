"""Tools the model may call, and the loop that runs them."""

import json
import queue
import threading
from collections.abc import Iterator

import pandas as pd

from src.faro_docs.facts import compute_facts
from src.faro_docs.messages_de import (
    DENKT_NACH,
    DENKT_NACH_EN,
    OLLAMA_NICHT_ERREICHBAR,
    OLLAMA_NICHT_ERREICHBAR_EN,
    ZU_VIELE_RUNDEN,
    ZU_VIELE_RUNDEN_EN,
    provenance_footer,
    translate_notes,
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
            "alle Tabellen NUR im zuletzt angehängten Dokument (normale Wahl bei "
            "einer einfachen Frage wie 'wie viele Zeilen'). Mit "
            "table='alle_dokumente' die Gesamtzahl über wirklich JEDES "
            "Dokument im Chat -- nur verwenden, wenn ausdrücklich nach allen "
            "Dokumenten zusammen gefragt wird."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {
                "type": "string",
                "description": "Tabellen-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente' (wirklich alles)",
            },
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
    {"type": "function", "function": {
        "name": "count_text_occurrences",
        "description": (
            "Zählt, wie oft ein Wort oder eine Zeichenfolge im Fließtext des "
            "Dokuments vorkommt (wie eine Strg+F-Suche, Groß-/Kleinschreibung "
            "wird ignoriert). NICHT für Zeilen oder Spalten einer Tabelle -- "
            "dafür count_rows oder find_rows verwenden. Mit document='alle' "
            "wird NUR im zuletzt angehängten Dokument gesucht (normale Wahl "
            "bei einer einfachen Frage); mit document='alle_dokumente' über "
            "wirklich jedes Dokument im Chat hinweg."
        ),
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Der gesuchte Text"},
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente' (wirklich alles)",
            },
        }, "required": ["search", "document"]},
    }},
]

SYSTEM_PROMPT = (
    "Du beantwortest Fragen zu hochgeladenen Dokumenten. Es kann sich um "
    "alles handeln: Rechnungen, Lieferscheine, Verträge, Berichte, Listen.\n\n"
    "Regeln:\n"
    "1. Zahlen, Anzahlen und Summen NIE selbst zählen oder addieren -- auch "
    "nicht, wie oft ein Wort oder eine Textstelle im Dokument vorkommt. Rufe "
    "das passende Werkzeug auf: count_rows/sum_column für Tabellenzeilen und "
    "-spalten, count_text_occurrences für ein Wort oder eine Zeichenfolge im "
    "Fließtext. Deine eigene Zählung oder Rechnung ist nicht verlässlich.\n"
    "2. Fragen nach der Anzahl der Tabellen, Blätter oder Dokumente IMMER mit "
    "list_tables beziehungsweise list_documents beantworten, nie schätzen.\n"
    "3. Eine Datei kann mehrere Tabellen enthalten, und es können mehrere "
    "Dateien hochgeladen sein. Prüfe das, bevor du eine Zahl nennst.\n"
    "4. In diesem Chat können mehrere Dokumente aus verschiedenen Nachrichten "
    "vorliegen, auch aus früheren Uploads, die nichts mehr mit der aktuellen "
    "Frage zu tun haben. count_rows mit table='alle' bezieht sich deshalb "
    "NUR auf das zuletzt angehängte Dokument (FAKTEN._zusammenfassung."
    "zuletzt_angehaengtes_dokument) -- die normale Wahl bei einer einfachen "
    "Frage wie \"wie viele Zeilen\". Nur wenn die Frage sich ausdrücklich auf "
    "mehrere Dokumente oder alle zusammen bezieht, table='alle_dokumente' "
    "verwenden oder FAKTEN._zusammenfassung.zeilen_gesamt übernehmen -- und "
    "NUR dann in der Antwort erwähnen. Bei einer einfachen Frage ohne "
    "Bezug auf \"alle\"/\"zusammen\" NICHT zusätzlich die Gesamtzahl über "
    "alle Dokumente nennen, auch wenn sie in FAKTEN sichtbar ist -- das "
    "verwirrt nur, wenn niemand danach gefragt hat.\n"
    "5. Für sum_column über mehrere Dokumente hinweg gibt es keinen "
    "Werkzeug-Modus, weil Spalten in verschiedenen Dokumenten unterschiedliche "
    "Bedeutung haben können. Frage nie mehrere Tabellen einzeln ab und "
    "addiere die Ergebnisse selbst -- das ist genau die Art von Rechnung, "
    "die nicht verlässlich ist (siehe Regel 1). Wenn eine Summe über mehrere "
    "Dokumente hinweg verlangt wird, nenne stattdessen die Summe je Dokument "
    "einzeln.\n"
    "6. Inhaltliche Fragen (Worum geht es? Wer ist der Absender? Was steht in "
    "Abschnitt 4?) direkt aus dem Dokumenttext beantworten. Für den Wert einer "
    "einzelnen Zeile oder Spalte (z. B. \"Welchen EAN-Code hat Zeile 6?\") "
    "IMMER get_row oder find_rows aufrufen -- auch wenn die Zeile oben in der "
    "Tabelle bereits sichtbar ist. Nie behaupten, ein Wert sei nicht "
    "verfügbar, ohne das Werkzeug versucht zu haben.\n"
    "7. Steht die Antwort nicht im Dokument, sage genau das (in der Sprache "
    "der Frage, z. B. „Das steht nicht im Dokument.“ auf Deutsch oder "
    "„That is not in the document.“ auf Englisch). Nichts erfinden.\n"
    "8. Antworte in der Sprache, in der die Frage gestellt wurde -- Deutsch "
    "bei einer deutschen Frage, Englisch bei einer englischen Frage, "
    "ebenso in jeder anderen Sprache. Nicht die Sprache des Dokuments "
    "annehmen, wenn die Frage in einer anderen Sprache gestellt wurde. "
    "In ganzen Sätzen, knapp."
)

_FORCE_ENGLISH = (
    "\n\nOVERRIDE (takes precedence over every rule above, including rule 8): "
    "you must answer only in English, in every single reply, no matter what "
    "language the question, the document, or its content is in. Never answer "
    "in German or any other language, even partially."
)


def _system_prompt_for(response_language: str) -> str:
    if response_language == "en":
        return SYSTEM_PROMPT + _FORCE_ENGLISH
    return SYSTEM_PROMPT


def _all_tables(documents: list[Document]) -> dict:
    return {table.id: table for document in documents for table in document.tables}


def run_tool(name: str, args: dict, documents: list[Document]):
    if name == "count_text_occurrences":
        search = args.get("search") or ""
        if not search:
            raise ValueError("Kein Suchbegriff angegeben.")
        needle = search.lower()
        document_arg = args.get("document")
        if document_arg == "alle_dokumente":
            return sum(document.text.lower().count(needle) for document in documents)
        if not document_arg or document_arg == "alle":
            # Same reasoning as count_rows's 'alle': Open WebUI hands back
            # every file ever attached in a chat on every turn, so a plain
            # "how many times does X appear" question must not silently
            # fold in an older, no-longer-relevant document.
            newest = documents[-1] if documents else None
            return newest.text.lower().count(needle) if newest else 0
        matches = [d for d in documents if d.id == document_arg]
        if not matches:
            raise ValueError(
                f"Unbekanntes Dokument „{document_arg}“. Gültige Dokumente: "
                f"{[d.id for d in documents]}"
            )
        return matches[0].text.lower().count(needle)

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

    if name == "count_rows" and args.get("table") == "alle_dokumente":
        return sum(table.row_count() for table in tables.values())

    if name == "count_rows" and args.get("table") == "alle":
        # Open WebUI hands back every file ever attached in a chat on every
        # turn, with no signal telling the pipe which are newly attached vs.
        # attached several messages ago -- a plain "how many rows" question
        # after uploading a new file must not silently fold in an older,
        # no-longer-relevant document. 'alle' therefore scopes to the most
        # recently attached document only; 'alle_dokumente' above is the
        # explicit escape hatch for a genuine cross-document question.
        newest = documents[-1] if documents else None
        return sum(table.row_count() for table in (newest.tables if newest else []))

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
    for index, document in enumerate(documents):
        text = document.text or ""
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "\n…[Text gekürzt]"
        marker = (
            " (zuletzt angehängt)" if len(documents) > 1 and index == len(documents) - 1 else ""
        )
        parts.append(f"## Dokument {document.id}: {document.filename}{marker}\n{text}")
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
# Renders as nothing in Markdown/HTML, so repeated keep-alive ticks after the
# first one stay invisible to the reader while still putting bytes on the
# wire -- Open WebUI/the browser can otherwise treat a long silent gap during
# Ollama's prefill as a dead connection and drop it (see _stream_with_heartbeat).
_KEEPALIVE = "​"


def _stream_with_heartbeat(client, model, messages, tools, interval, num_ctx):
    """Ollama prefills a long document before emitting anything; a silent gap
    that long drops the browser connection, so emit a heartbeat while waiting."""
    channel: queue.Queue = queue.Queue()

    def worker():
        try:
            for chunk in client.chat(
                model=model, messages=messages, tools=tools, stream=True,
                options={"num_ctx": num_ctx},
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
    response_language: str = "",
    heartbeat_interval: float = 0.5,
    num_ctx: int = 16384,
) -> Iterator[str]:
    """Plain sync generator -- async pipes never signal completion (open-webui#20196,
    confirmed still present in a real 0.11.3 install: an async pipe's final
    message does arrive, but the UI's "Stop" button never clears and the
    chat is stuck looking like it's still generating).

    response_language: "" (default) lets the model match whatever language
    the question was asked in, and keeps this pipe's own fixed messages in
    German. Set to "en" to force English everywhere -- the model's answer,
    this pipe's own status/error messages, and the notes ingestion produces
    -- for local testing by someone who doesn't read German. The real
    office deployment leaves this at its default.

    heartbeat_interval: how often (seconds) to poll for output while Ollama
    prefills. Open WebUI (functions.py) iterates this generator with a
    plain synchronous `for` loop inside an async function, so every single
    poll genuinely blocks its *entire* event loop -- not just this chat's
    request -- for up to this many seconds at a time (confirmed by reading
    Open WebUI's own source). A long prefill is many such polls back to
    back, which is why the browser's own websocket can show "connection
    lost, reconnecting" for the whole wait even though the request itself
    is fine and completes correctly. Kept short so each individual freeze
    is brief enough that it usually doesn't trip the websocket's own
    timeout, rather than a few long freezes that reliably do. Also tunable
    for tests, to run them fast.

    num_ctx: Ollama's context window in tokens, passed explicitly on every
    request. Ollama silently defaults an unconfigured model to 4096 tokens
    regardless of what the model itself supports (confirmed via `ollama ps`)
    -- a real document's extracted text plus its table markdown plus FAKTEN
    can exceed that on a table with a couple hundred rows, at which point
    Ollama quietly drops the *oldest* part of the prompt to fit, and the
    model answers confidently from a table it never actually saw in full.
    16384 comfortably covers the default MAX_TEXT_CHARS (40000 chars); raise
    both together if MAX_TEXT_CHARS is raised.
    """
    import ollama

    client = ollama.Client(host=host)
    context = build_context(documents, max_text_chars=max_text_chars)
    messages = [
        {"role": "system", "content": _system_prompt_for(response_language)},
        {"role": "user", "content": f"DOKUMENTE:\n{context}\n\nFRAGE: {question}"},
    ]
    # Not gated on _all_tables(documents): count_text_occurrences works on a
    # document's free text and needs no table at all -- a pure-text upload
    # (no extractable table) must still get tools, not be silently limited
    # to the model's own unreliable reading-based counting.
    tools = TOOL_SCHEMAS if documents else None
    used_tool_names: set[str] = set()

    for _ in range(max_rounds):
        content = ""
        tool_calls = None
        seen_output = False
        heartbeat_shown = False
        try:
            for item in _stream_with_heartbeat(
                client, model, messages, tools,
                interval=heartbeat_interval, num_ctx=num_ctx,
            ):
                if item is _HEARTBEAT:
                    if not seen_output:
                        if not heartbeat_shown:
                            template = (
                                DENKT_NACH_EN if response_language == "en" else DENKT_NACH
                            )
                            yield template + "\n\n"
                            heartbeat_shown = True
                        else:
                            yield _KEEPALIVE
                    continue
                seen_output = True
                piece = item.get("message", {}).get("content", "")
                if piece:
                    content += piece
                    yield piece
                if item.get("message", {}).get("tool_calls"):
                    tool_calls = item["message"]["tool_calls"]
        except Exception as error:
            template = (
                OLLAMA_NICHT_ERREICHBAR_EN if response_language == "en" else OLLAMA_NICHT_ERREICHBAR
            )
            yield "\n\n" + template.format(host=host)
            yield f"\n\n_({error})_"
            return

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )
        if not tool_calls:
            sources = set()
            if used_tool_names & {"count_text_occurrences"}:
                sources.add("textsuche")
            if used_tool_names - {"count_text_occurrences", "list_documents"}:
                sources.add("tabelle")
            if not sources:
                sources = {"text"}
            notes = [n for d in documents for n in d.notes]
            notes += [n for d in documents for t in d.tables for n in t.notes]
            notes = translate_notes(notes, response_language or "de")
            yield provenance_footer(sources, notes, language=response_language or "de")
            return

        for call in tool_calls:
            name = call["function"]["name"]
            used_tool_names.add(name)
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

    yield "\n\n" + (ZU_VIELE_RUNDEN_EN if response_language == "en" else ZU_VIELE_RUNDEN)
