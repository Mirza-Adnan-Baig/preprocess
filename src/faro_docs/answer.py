"""Tools the model may call, and the loop that runs them."""

import json
import queue
import threading
from collections.abc import Iterator

import pandas as pd

from src.faro_docs.facts import compute_facts
from src.faro_docs.german import parse_number, to_numeric_series
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
        "name": "count_matching_rows",
        "description": (
            "Zählt, wie viele Zeilen einer Tabelle in einer Spalte einen Text "
            "enthalten -- die verlässliche Wahl für \"wie viele X gibt es\" auf "
            "einer echten Tabelle (z. B. wie viele Zeilen in der Spalte "
            "„Bezeichnung“ „Zubehör“ enthalten). Liefert die exakte "
            "Gesamtzahl, anders als find_rows, das nur eine begrenzte "
            "Vorschau zurückgibt und bei vielen Treffern zu niedrig wäre."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "contains": {"type": "string"},
            "mode": {
                "type": "string",
                "description": (
                    "contains (Standard), starts_with, ends_with, empty "
                    "oder not_empty -- starts_with z. B. für \"wie viele EANs "
                    "beginnen mit 0\", empty für \"wie viele Zeilen haben "
                    "kein Barcode\""
                ),
            },
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
    {"type": "function", "function": {
        "name": "search_text",
        "description": (
            "Durchsucht den GESAMTEN Dokumenttext nach einem Begriff und gibt "
            "die Fundstellen mit Textumgebung zurück. WICHTIG: oben im Prompt "
            "steht bei langen Dokumenten nur der Anfang des Textes -- mit "
            "diesem Werkzeug kommst du an JEDE Stelle des Dokuments heran, "
            "auch an Seite 30 von 41. Immer verwenden, wenn im sichtbaren "
            "Ausschnitt nichts steht oder \"[Text gekürzt]\" erscheint, bevor "
            "du sagst, etwas stehe nicht im Dokument."
        ),
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Gesuchter Begriff"},
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente'",
            },
            "max_treffer": {
                "type": "integer",
                "description": "Wie viele Fundstellen zurückgegeben werden (Standard 5)",
            },
        }, "required": ["search", "document"]},
    }},
    {"type": "function", "function": {
        "name": "query_table",
        "description": (
            "Die flexible Tabellenabfrage: filtern, rechnen, sortieren. "
            "Für alles, was über einfaches Zählen hinausgeht, z. B. \"welche "
            "Artikel kosten mehr als 10 Euro\", \"was kosten alle Zubehörteile "
            "zusammen\", \"die 5 teuersten Positionen\", \"welcher Artikel hat "
            "die größte Menge\", \"wie viele Zeilen haben kein Barcode\". "
            "filters verknüpft mehrere Bedingungen mit UND. Ohne aggregate "
            "kommen die passenden Zeilen zurück, mit aggregate die berechnete "
            "Zahl."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Tabellen-Id"},
            "filters": {
                "type": "array",
                "description": (
                    "Liste von Bedingungen, alle müssen zutreffen. Jede: "
                    "{\"column\": Spaltenname, \"op\": contains|equals|"
                    "starts_with|ends_with|gt|lt|gte|lte|empty|not_empty, "
                    "\"value\": Wert}"
                ),
                "items": {"type": "object", "properties": {
                    "column": {"type": "string"},
                    "op": {"type": "string"},
                    "value": {"type": "string"},
                }},
            },
            "aggregate": {
                "type": "object",
                "description": (
                    "Optional. {\"func\": count|sum|avg|min|max|distinct, "
                    "\"column\": Spaltenname}. count braucht keine Spalte."
                ),
                "properties": {
                    "func": {"type": "string"},
                    "column": {"type": "string"},
                },
            },
            "sort_by": {"type": "string", "description": "Optional: nach dieser Spalte sortieren"},
            "sort_desc": {"type": "boolean", "description": "true = absteigend (größte zuerst)"},
            "limit": {"type": "integer", "description": "Wie viele Zeilen zurückkommen (Standard 20)"},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "column_stats",
        "description": (
            "Überblick über eine Spalte: Anzahl Werte, wie viele verschiedene, "
            "wie viele leer, die häufigsten Werte, und bei Zahlenspalten "
            "Summe/Min/Max/Durchschnitt. Gut für \"wie viele verschiedene "
            "Artikel gibt es\", \"fehlen Werte\", \"welcher Wert kommt am "
            "häufigsten vor\"."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "find_duplicates",
        "description": (
            "Findet Werte, die in einer Spalte mehrfach vorkommen -- z. B. "
            "doppelte EAN-Codes oder doppelt erfasste Artikelnummern in einer "
            "Preisliste."
        ),
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"}, "column": {"type": "string"},
            "limit": {"type": "integer", "description": "Wie viele Duplikate aufgelistet werden (Standard 20)"},
        }, "required": ["table", "column"]},
    }},
    {"type": "function", "function": {
        "name": "document_info",
        "description": (
            "Aufbau eines Dokuments: Dateiname, Dateityp, Seitenzahl bei PDF, "
            "Länge des Textes, und jede erkannte Tabelle mit Spalten und "
            "Zeilenzahl. Für \"wie viele Seiten hat das Dokument\" oder "
            "\"was ist das überhaupt für eine Datei\"."
        ),
        "parameters": {"type": "object", "properties": {
            "document": {
                "type": "string",
                "description": "Dokument-Id, 'alle' (nur neuestes Dokument), oder 'alle_dokumente'",
            },
        }, "required": ["document"]},
    }},
]

SYSTEM_PROMPT = (
    "Du beantwortest Fragen zu hochgeladenen Dokumenten. Es kann sich um "
    "alles handeln: Rechnungen, Lieferscheine, Verträge, Berichte, Listen.\n\n"
    "Regeln:\n"
    "1. Zahlen, Anzahlen und Summen NIE selbst zählen oder addieren -- auch "
    "nicht, wie oft ein Wort oder eine Textstelle im Dokument vorkommt. Rufe "
    "das passende Werkzeug auf: count_rows/sum_column für Tabellenzeilen und "
    "-spalten. Für \"wie viele X gibt es\" (z. B. wie viele Zubehörteile), "
    "wenn X in einer Tabellenspalte vorkommt (Artikel, Bezeichnung, o. Ä.), "
    "IMMER count_matching_rows auf dieser Spalte verwenden, NICHT "
    "count_text_occurrences -- eine Zeile ist ein echtes Produkt, während "
    "eine reine Textsuche durch wiederholte Kopf-/Fußzeilen auf jeder Seite "
    "oder durch Zeilenumbrüche mitten im Wort verfälscht werden kann. "
    "count_text_occurrences NUR verwenden, wenn es keine passende Tabelle "
    "gibt oder ausdrücklich nach dem Fließtext gefragt wird. Deine eigene "
    "Zählung oder Rechnung ist nicht verlässlich.\n"
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
    "6b. Der oben sichtbare Dokumenttext ist bei langen Dokumenten gekürzt "
    "(erkennbar an „[… Zeichen aus der Mitte ausgelassen …]“ oder „[… weitere "
    "Zeilen nicht angezeigt …]“). Der VOLLSTÄNDIGE Text ist trotzdem "
    "erreichbar: search_text durchsucht ihn ganz und liefert die Fundstelle "
    "mit Umgebung. Bevor du sagst, etwas stehe nicht im Dokument, IMMER "
    "zuerst search_text mit einem passenden Stichwort versuchen.\n"
    "6c. Für alles, was über einfaches Zählen hinausgeht, query_table "
    "verwenden: Filter nach Zahl (\"teurer als 10 Euro\"), gefilterte Summen "
    "(\"was kosten alle X zusammen\"), Sortieren und Top-N (\"die 5 teuersten "
    "Positionen\", \"größte Menge\"), leere Felder (op=empty). column_stats "
    "für \"wie viele verschiedene\", \"welcher Wert kommt am häufigsten vor\", "
    "\"fehlen Werte\". find_duplicates für doppelte EANs oder "
    "Artikelnummern. document_info für Seitenzahl und Aufbau der Datei.\n"
    "7. Steht die Antwort nach einer ehrlichen Suche wirklich nicht im "
    "Dokument, sage genau das (in der Sprache der Frage, z. B. „Das steht "
    "nicht im Dokument.“ auf Deutsch oder „That is not in the document.“ auf "
    "Englisch). Nichts erfinden.\n"
    "8. Antworte in der Sprache, in der die Frage gestellt wurde -- Deutsch "
    "bei einer deutschen Frage, Englisch bei einer englischen Frage, "
    "ebenso in jeder anderen Sprache. Nicht die Sprache des Dokuments "
    "annehmen, wenn die Frage in einer anderen Sprache gestellt wurde. "
    "In ganzen Sätzen, knapp.\n"
    "9. Liefert ein Werkzeugaufruf einen Fehler (ein „fehler“-Feld im "
    "Ergebnis), NIE trotzdem eine Zahl oder einen Wert erfinden oder raten. "
    "Entweder das Werkzeug mit korrigierten Argumenten erneut aufrufen, "
    "oder in der Antwort klar sagen, dass die Anfrage nicht sicher "
    "beantwortet werden konnte."
)

# Repeated directly after the question, not only in the system prompt.
# On a long document the rules sit thousands of tokens away by the time
# the model reaches the question, and a smaller model simply stops acting
# on them -- measured on a 50-page catalogue, it counted "Zubehör" by eye
# and said 12 where the real answer was 554, without calling any tool at
# all, while the same model used tools correctly on a short document.
# The last thing before generation gets the most attention, so the one
# rule that matters most is restated there.
_REMINDER = (
    "ERINNERUNG: Zähle, summiere und suche NIE selbst im obigen Text oder "
    "in der Tabelle -- der Ausschnitt oben ist bei langen Dokumenten "
    "unvollständig, eigenes Zählen ist dort immer falsch. Rufe ein "
    "Werkzeug auf: count_matching_rows oder query_table für Zeilen einer "
    "Tabelle, count_text_occurrences oder search_text für den Fließtext, "
    "document_info für Seitenzahl und Aufbau. Erst danach antworten."
)

_SEARCH_FIRST = (
    "STOP. Du behauptest, etwas stehe nicht im Dokument, ohne danach "
    "gesucht zu haben. Der oben sichtbare Ausschnitt ist bei langen "
    "Dokumenten unvollständig -- das ist keine Grundlage für diese "
    "Aussage. Rufe JETZT search_text mit einem passenden Stichwort auf "
    "(wirklich aufrufen, nicht beschreiben). Erst wenn die Suche nichts "
    "findet, darfst du sagen, dass es nicht im Dokument steht."
)

_TOOL_REQUIRED = (
    "STOP. Du hast kein Werkzeug aufgerufen, sondern selbst gezählt oder "
    "geschätzt. Das ist bei dieser Frage immer falsch, weil der oben "
    "sichtbare Ausschnitt unvollständig ist. Rufe JETZT genau ein Werkzeug "
    "richtig auf (nicht als Text beschreiben, sondern wirklich aufrufen) "
    "und antworte erst mit dessen Ergebnis. Wenn wirklich kein Werkzeug "
    "passt, sage klar, dass du es nicht sicher beantworten kannst -- nenne "
    "keine selbst gezählte Zahl."
)

# Question wordings that can only be answered correctly by a tool. Kept
# deliberately broad: the cost of a false positive is one short answer
# being held back for a moment, the cost of a false negative is a
# confidently wrong number.
_QUANTITATIVE_HINTS = (
    "wie viele", "wieviele", "wie oft", "anzahl", "summe", "gesamt",
    "durchschnitt", "teuerst", "billigst", "größte", "groesste", "kleinste",
    "höchste", "hoechste", "niedrigste", "doppelt", "duplikat", "fehlen",
    "fehlt", "leer", "seiten", "how many", "how much", "how often", "count",
    "total", "sum of", "average", "most expensive", "cheapest", "highest",
    "lowest", "duplicate", "missing", "empty", "pages",
    # A price question rarely says "sum": "was kosten alle X zusammen" and
    # "what do all the X cost together" both slipped through and were
    # answered without a tool -- found by sweeping real question wordings.
    "kosten", "kostet", "preis", "betrag", "wert", "zusammen", "insgesamt",
    "cost", "price", "worth", "together", "altogether", "combined",
)

# Phrases that claim something isn't there. Claiming absence without ever
# having searched is the other half of the same problem: on a long
# document the visible extract is incomplete, so "it doesn't say" is
# unfounded unless search_text actually came back empty.
_ABSENCE_CLAIMS = (
    "nicht im dokument", "steht nicht", "keine angabe", "nicht enthalten",
    "nicht erwähnt", "nicht genannt", "kein hinweis", "nicht auffindbar",
    "not in the document", "no explicit", "not mentioned", "does not contain",
    "doesn't contain", "could not find", "couldn't find", "no information",
    "there is no", "not specified", "not stated", "unable to find",
)

_SEARCH_TOOLS = {
    "search_text", "count_text_occurrences", "find_rows",
    "count_matching_rows", "query_table", "get_row",
}


def needs_a_tool(question: str) -> bool:
    """Whether this question can only be answered correctly by a tool."""
    lowered = (question or "").lower()
    return any(hint in lowered for hint in _QUANTITATIVE_HINTS)


def claims_absence(text: str) -> bool:
    """Whether an answer asserts the document doesn't say something."""
    lowered = (text or "").lower()
    return any(claim in lowered for claim in _ABSENCE_CLAIMS)


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


def _resolve_documents(documents: list[Document], document_arg) -> list[Document]:
    """Turn a document argument into the documents it refers to.

    Mirrors count_rows's 'alle' semantics: Open WebUI hands back every file
    ever attached in a chat on every turn, so a plain question defaults to
    the most recently attached document only, and 'alle_dokumente' is the
    explicit opt-in to every one of them.
    """
    if document_arg == "alle_dokumente":
        return list(documents)
    if not document_arg or document_arg == "alle":
        return documents[-1:] if documents else []
    matches = [d for d in documents if d.id == document_arg]
    if not matches:
        raise ValueError(
            f"Unbekanntes Dokument „{document_arg}“. Gültige Dokumente: "
            f"{[d.id for d in documents]}"
        )
    return matches


def _numeric_series(table, column: str) -> pd.Series:
    """A column as real numbers, using the format already decided for it."""
    info = table.columns.get(column)
    if info is not None and info.numeric_style in {"german", "english", "integer"}:
        return to_numeric_series(table.frame[column], info.numeric_style)
    # Column was kept as text (an identifier, or mixed content). Comparing it
    # numerically is still allowed where the values happen to parse, but a
    # column with nothing numeric in it must say so rather than compare
    # everything against NaN and silently return zero matches.
    coerced = pd.to_numeric(table.frame[column], errors="coerce")
    if len(table.frame) and coerced.notna().sum() == 0:
        raise ValueError(
            f"Spalte „{column}“ enthält keine Zahlen -- ein Zahlenvergleich "
            f"ist hier nicht möglich. Für Text „contains“ oder „equals“ verwenden."
        )
    return coerced


def _check_column(table, column: str) -> str:
    """Validate a column name, naming the real ones when it's wrong."""
    if column not in table.frame.columns:
        raise ValueError(
            f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
        )
    return column


def _parse_threshold(value):
    """Read a comparison value written in either convention.

    The question can come from either side: a German user types 10,5 and a
    model writing English types 10.5. Trying German first read "10.5" as
    ten-thousand-five (dot as a thousands separator), so a "price over
    10.5" filter silently matched nothing and the model then invented an
    answer -- confirmed live. The separator present decides the reading.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if "," in text and "." in text:
        # Both present, so one groups and one decimates: the one appearing
        # last is the decimal separator (1.234,56 German / 1,234.56 English).
        return parse_number(
            text, "german" if text.rfind(",") > text.rfind(".") else "english"
        )
    if "," in text:
        return parse_number(text, "german")
    if "." in text:
        # A lone dot in a threshold someone typed is a decimal point far more
        # often than a German thousands separator ("price over 10.5"), so it
        # is read that way; "1.234" meaning 1234 is the accepted edge case.
        return parse_number(text, "english")
    return parse_number(text, "integer")


def _display(value) -> str:
    """Render a cell value the way it should be read back.

    A whole number living in a float column stringifies as "10000.0",
    which looks like a different value from the 10000 printed in the
    document -- misleading in a duplicate list or a most-common-values
    list, where the point is to quote the value exactly.
    """
    if isinstance(value, float) and not isinstance(value, bool):
        if pd.isna(value):
            return ""
        if float(value).is_integer():
            return str(int(value))
    return str(value)


def _column_examples(frame, column: str, count: int = 3) -> list[str]:
    values = frame[column].astype(str).str.strip()
    return [v for v in values[values.ne("")].unique()[:count]]


def _positive_int(value, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _require(args: dict, key: str, tool: str):
    """Read a required tool argument, or raise an error the model can act
    on. A bare args[key] KeyError's own text is just "'column'" -- useless
    for a model deciding whether to retry -- and a small model has been
    observed answering with a made-up number after exactly this kind of
    silent-looking failure instead of retrying or admitting it couldn't
    tell. An explicit, instructive message makes a correct retry likelier.
    """
    value = args.get(key)
    if value in (None, ""):
        raise ValueError(
            f"„{tool}“ wurde ohne das Pflichtfeld „{key}“ aufgerufen. "
            f"Rufe das Werkzeug erneut auf und gib „{key}“ mit an."
        )
    return value


def run_tool(name: str, args: dict, documents: list[Document]):
    if name == "count_text_occurrences":
        needle = str(_require(args, "search", name)).lower()
        scope = _resolve_documents(documents, args.get("document"))
        return sum(document.text.lower().count(needle) for document in scope)

    if name == "search_text":
        needle = str(_require(args, "search", name)).lower()
        scope = _resolve_documents(documents, args.get("document"))
        try:
            max_hits = int(args.get("max_treffer") or 5)
        except (TypeError, ValueError):
            max_hits = 5
        max_hits = max(1, min(max_hits, 20))

        hits: list[dict] = []
        total = 0
        for document in scope:
            text = document.text or ""
            lowered = text.lower()
            start = 0
            while True:
                position = lowered.find(needle, start)
                if position < 0:
                    break
                total += 1
                if len(hits) < max_hits:
                    left = max(0, position - 120)
                    right = min(len(text), position + len(needle) + 120)
                    snippet = " ".join(text[left:right].split())
                    hits.append({
                        "dokument": document.id,
                        "zeichen_position": position,
                        "auszug": ("…" if left > 0 else "") + snippet
                        + ("…" if right < len(text) else ""),
                    })
                start = position + len(needle)
        if total:
            return {"treffer_gesamt": total, "stellen": hits}
        # Nothing found. The questions that lead here -- who sent this, what
        # is the invoice number, which company -- are almost always answered
        # in the first block of the document, and a model that searched the
        # wrong word otherwise concludes the document doesn't say (measured:
        # it decided a 50-page list had no company name on it, while the
        # name was in the opening line). Hand that block back instead.
        opening = " ".join((scope[0].text or "")[:600].split()) if scope else ""
        return {
            "treffer_gesamt": 0,
            "stellen": [],
            "hinweis": (
                f"„{search}“ kommt im Text nicht vor. Vielleicht war das "
                "Stichwort falsch gewählt -- hier ist der Anfang des "
                "Dokuments, wo Absender, Nummern und Datum normalerweise "
                "stehen. Mit einem anderen Stichwort erneut suchen, bevor "
                "du sagst, etwas stehe nicht im Dokument."
            ),
            "dokumentanfang": opening,
        }

    if name == "document_info":
        scope = _resolve_documents(documents, args.get("document"))
        return {
            document.id: {
                "dateiname": document.filename,
                "dateityp": document.media_type,
                "seiten": document.page_count or "keine Seiten (kein PDF)",
                "textlaenge_zeichen": len(document.text or ""),
                "tabellen": {
                    table.id: {
                        "bezeichnung": table.label,
                        "zeilen": table.row_count(),
                        "spalten": [str(c) for c in table.frame.columns],
                    }
                    for table in document.tables
                },
                "hinweise": list(document.notes),
            }
            for document in scope
        }

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
        # count_rows accepts 'alle', so a model reasonably assumes every
        # table tool does -- and then gets "unknown table" and gives up.
        # Measured over a twelve-question sweep, this one mismatch caused
        # most of the failures. When the intent is unambiguous (the scoped
        # document has exactly one table, which is the normal case) it is
        # resolved instead of refused; when it is genuinely ambiguous the
        # error names the real ids so the retry can be correct.
        candidates = (
            list(tables.values())
            if table_id == "alle_dokumente"
            else [t for d in documents[-1:] for t in d.tables]
        )
        if table_id in (None, "", "alle", "alle_dokumente") and len(candidates) == 1:
            table = candidates[0]
        else:
            raise ValueError(
                f"Unbekannte Tabelle „{table_id}“. Gültige Tabellen: {list(tables)}. "
                "Rufe das Werkzeug erneut mit einer dieser Ids auf."
            )
    else:
        table = tables[table_id]

    if name == "count_rows":
        return table.row_count()

    if name == "sum_column":
        column = _check_column(table, _require(args, "column", name))
        numeric = _numeric_series(table, column)
        # Say what was summed, not just the number. Confirmed live: asked
        # what one group of parts costs, a model called this tool with no
        # filter and reported the whole table's total as that group's total
        # (660 instead of 108). The number wasn't invented -- its scope was
        # mislabelled -- so the scope now travels with the number.
        return {
            "spalte": column,
            "summe": float(numeric.sum()),
            "zeilen_einbezogen": int(table.row_count()),
            "hinweis": (
                f"Summe über ALLE {table.row_count()} Zeilen der Tabelle, ohne "
                "Filter. Wenn nur bestimmte Zeilen gemeint sind (z. B. nur ein "
                "Artikeltyp), stattdessen query_table mit filters verwenden -- "
                "diese Zahl wäre sonst falsch beschriftet."
            ),
        }

    if name == "get_row":
        return table.frame.iloc[int(_require(args, "index", name))].to_dict()

    if name in {"find_rows", "count_matching_rows"}:
        column = _require(args, "column", name)
        if column not in table.frame.columns:
            raise ValueError(
                f"Spalte „{column}“ gibt es nicht. Vorhanden: {list(table.frame.columns)}"
            )
        if "contains" not in args:
            _require(args, "contains", name)  # raises, naming the missing field
        needle = str(args.get("contains") or "").lower().strip()
        mode = str(args.get("mode") or "contains").lower()
        if mode not in {"contains", "starts_with", "ends_with", "empty", "not_empty"}:
            raise ValueError(
                f"Unbekannter mode „{mode}“. Möglich: contains, starts_with, "
                "ends_with, empty, not_empty."
            )
        if mode in {"empty", "not_empty"}:
            # "How many rows have no barcode" naturally comes to this tool
            # with mode='empty'; refusing it just sent the model in circles.
            values = table.frame[column]
            is_empty = values.isna() | values.astype(str).str.strip().eq("")
            blank_mask = is_empty if mode == "empty" else ~is_empty
            if name == "count_matching_rows":
                return int(blank_mask.sum())
            return table.frame[blank_mask].head(50).to_dict(orient="records")
        # Searching for the *text* "nan"/"leer" is an attempt to find empty
        # cells, and it silently finds nothing: an empty cell is missing
        # data, not the word "nan" (pandas keeps it missing through
        # astype(str), so the match fails). Confirmed live: a model asked
        # "how many rows have no barcode", searched for "nan", got 0, and
        # concluded every row had one -- while two genuinely did not.
        if needle in {"nan", "none", "null", "leer", "empty", ""}:
            raise ValueError(
                "Leere Felder lassen sich so nicht finden. Dafür query_table "
                f"verwenden: filters=[{{\"column\": \"{column}\", \"op\": \"empty\"}}] "
                "(oder \"not_empty\" für gefüllte Felder)."
            )
        as_text = table.frame[column].astype(str).str.lower()
        mask = {
            # "How many EANs start with a zero" went to this tool, not to
            # query_table, and `contains` counted a zero anywhere in the
            # code -- 1369 instead of 169. Prefix and suffix matching lives
            # here too now, so the natural tool choice is also the correct
            # one.
            "contains": lambda: as_text.str.contains(needle, na=False, regex=False),
            "starts_with": lambda: as_text.str.startswith(needle, na=False),
            "ends_with": lambda: as_text.str.endswith(needle, na=False),
        }[mode]()
        if name == "count_matching_rows":
            total = int(mask.sum())
            if mode == "contains":
                # "How many EANs start with a zero" comes to this tool, not
                # to query_table, and `contains` answers a different
                # question: a zero anywhere in the code (1369) rather than
                # at the front (169). Both readings are cheap to compute,
                # so both are returned, each labelled, rather than
                # answering only the one that was literally asked for.
                prefix_total = int(as_text.str.startswith(needle, na=False).sum())
                if prefix_total != total:
                    return {
                        "enthaelt_irgendwo": total,
                        "beginnt_damit": prefix_total,
                        "hinweis": (
                            "Bei einer Frage nach „beginnt mit“ / „starts "
                            "with“ die Zahl „beginnt_damit“ nennen, sonst "
                            "„enthaelt_irgendwo“."
                        ),
                    }
            return total
        return table.frame[mask].head(50).to_dict(orient="records")

    if name == "column_stats":
        column = _check_column(table, _require(args, "column", name))
        values = table.frame[column]
        filled = values[values.astype(str).str.strip().ne("") & values.notna()]
        stats = {
            "zeilen_gesamt": int(len(values)),
            "gefuellt": int(len(filled)),
            "leer": int(len(values) - len(filled)),
            "verschiedene_werte": int(filled.nunique()),
            "haeufigste_werte": [
                {"wert": _display(value), "anzahl": int(count)}
                for value, count in filled.value_counts().head(5).items()
            ],
        }
        info = table.columns.get(column)
        if info is not None and info.numeric_style in {"german", "english", "integer"}:
            numbers = to_numeric_series(values, info.numeric_style).dropna()
            if len(numbers):
                stats["zahlen"] = {
                    "summe": float(numbers.sum()),
                    "min": float(numbers.min()),
                    "max": float(numbers.max()),
                    "durchschnitt": float(numbers.mean()),
                }
        else:
            stats["hinweis"] = (
                "Textspalte (z. B. Bezeichnung oder eine Kennnummer wie EAN) -- "
                "bewusst nicht als Zahl behandelt, deshalb keine Summe."
            )
        return stats

    if name == "find_duplicates":
        column = _check_column(table, _require(args, "column", name))
        limit = _positive_int(args.get("limit"), default=20, maximum=100)
        values = table.frame[column].map(_display).str.strip()
        filled = values[values.ne("")]
        counts = filled.value_counts()
        duplicated = counts[counts > 1]
        return {
            "spalte": column,
            "werte_mit_mehrfachvorkommen": int(len(duplicated)),
            "betroffene_zeilen_gesamt": int(duplicated.sum()),
            "beispiele": [
                {"wert": _display(value), "anzahl": int(count)}
                for value, count in duplicated.head(limit).items()
            ],
        }

    if name == "query_table":
        frame = table.frame
        mask = pd.Series(True, index=frame.index)
        applied: list[str] = []
        for condition in args.get("filters") or []:
            if not isinstance(condition, dict):
                continue
            column = _check_column(table, _require(condition, "column", name))
            operator = str(condition.get("op") or "contains").lower()
            raw_value = condition.get("value")
            column_values = frame[column]

            if operator in {"empty", "not_empty"}:
                is_empty = column_values.isna() | column_values.astype(str).str.strip().eq("")
                condition_mask = is_empty if operator == "empty" else ~is_empty
            elif operator in {"contains", "equals", "starts_with", "ends_with"}:
                text = str("" if raw_value is None else raw_value).lower()
                as_text = column_values.astype(str).str.lower().str.strip()
                condition_mask = {
                    "contains": lambda: as_text.str.contains(text, na=False, regex=False),
                    "equals": lambda: as_text.eq(text),
                    # A prefix or suffix question is common on codes -- EAN
                    # country prefixes, article-number ranges, "everything
                    # starting with 33". Without these, "how many EANs start
                    # with a zero" had no correct call at all: `contains`
                    # would match a zero anywhere in the code.
                    "starts_with": lambda: as_text.str.startswith(text, na=False),
                    "ends_with": lambda: as_text.str.endswith(text, na=False),
                }[operator]()
            elif operator in {"gt", "lt", "gte", "lte"}:
                numbers = _numeric_series(table, column)
                threshold = _parse_threshold(raw_value)
                if threshold is None:
                    raise ValueError(
                        f"„{raw_value}“ ist keine Zahl, mit der sich vergleichen lässt."
                    )
                comparison = {
                    "gt": numbers > threshold,
                    "lt": numbers < threshold,
                    "gte": numbers >= threshold,
                    "lte": numbers <= threshold,
                }[operator]
                condition_mask = comparison.fillna(False)
            else:
                raise ValueError(
                    f"Unbekannter Operator „{operator}“. Möglich: contains, "
                    "equals, starts_with, ends_with, gt, lt, gte, lte, "
                    "empty, not_empty."
                )
            mask &= condition_mask
            applied.append(
                f"{column} {operator}"
                if operator in {"empty", "not_empty"}
                else f"{column} {operator} {raw_value}"
            )

        selected = frame[mask]

        # A filter that matches nothing is the most common way one of these
        # calls goes wrong -- usually op=equals against a descriptive column
        # where only part of the text was given ("Zubehör" vs "Zubehör Akku
        # Typ 3"). Confirmed live: the old error blamed the column for having
        # no numbers, which is not what went wrong, and the model gave up and
        # invented a total instead of retrying. Say what actually happened and
        # show real values from the column so the next call can be corrected.
        if applied and not len(selected):
            # Work out whether a partial match would have found something and
            # say so concretely -- "op='contains' would return 12 rows" is a
            # far stronger correction than advice, and it costs one pass over
            # the column to compute.
            retry_counts = {}
            for condition in args.get("filters") or []:
                if not isinstance(condition, dict):
                    continue
                target = condition.get("column")
                if target not in frame.columns or condition.get("value") in (None, ""):
                    continue
                if str(condition.get("op") or "").lower() != "equals":
                    continue
                would_match = int(
                    frame[target].astype(str).str.lower()
                    .str.contains(str(condition["value"]).lower(), na=False, regex=False)
                    .sum()
                )
                if would_match:
                    retry_counts[target] = would_match

            hinweis = (
                "Kein Treffer. KEINE Zahl erfinden -- den Aufruf korrigieren "
                "und erneut aufrufen."
            )
            if retry_counts:
                spalten = ", ".join(
                    f"„{column}“ ({count} Zeilen)" for column, count in retry_counts.items()
                )
                hinweis = (
                    f"Kein Treffer mit op='equals'. Mit op='contains' gäbe es "
                    f"Treffer in {spalten}. Rufe query_table JETZT erneut auf, "
                    f"mit op='contains' statt 'equals'. KEINE Zahl erfinden."
                )
            return {
                "treffer_gesamt": 0,
                "filter": applied,
                "hinweis": hinweis,
                "beispielwerte": {
                    condition["column"]: _column_examples(frame, condition["column"])
                    for condition in (args.get("filters") or [])
                    if isinstance(condition, dict)
                    and condition.get("column") in frame.columns
                },
            }

        aggregate = args.get("aggregate")
        if isinstance(aggregate, dict) and aggregate.get("func"):
            function = str(aggregate["func"]).lower()
            if function == "count":
                return {"filter": applied, "anzahl": int(len(selected))}
            column = _check_column(table, _require(aggregate, "column", name))
            if function == "distinct":
                return {
                    "filter": applied,
                    "verschiedene_werte": int(selected[column].astype(str).nunique()),
                }
            numbers = _numeric_series(table, column)[mask].dropna()
            if not len(numbers):
                raise ValueError(
                    f"In Spalte „{column}“ stehen bei den gefilterten Zeilen "
                    f"keine auswertbaren Zahlen. Beispielwerte: "
                    f"{_column_examples(frame, column)}"
                )
            result = {
                "sum": float(numbers.sum()),
                "avg": float(numbers.mean()),
                "min": float(numbers.min()),
                "max": float(numbers.max()),
            }.get(function)
            if result is None:
                raise ValueError(
                    f"Unbekannte Funktion „{function}“. Möglich: count, sum, "
                    "avg, min, max, distinct."
                )
            payload = {"filter": applied, "spalte": column, function: result}
            if not applied:
                # Same trap as sum_column: an aggregate with no filter is the
                # whole table, and a model asked about one group of parts has
                # been seen reporting that as the group's figure.
                payload["zeilen_einbezogen"] = int(len(frame))
                payload["hinweis"] = (
                    f"Ohne Filter gerechnet, also über ALLE {len(frame)} Zeilen. "
                    "Wenn nach einer bestimmten Gruppe gefragt wurde (z. B. ein "
                    "Artikeltyp), erneut aufrufen mit filters, sonst ist diese "
                    "Zahl falsch beschriftet."
                )
            return payload

        sort_by = args.get("sort_by")
        if sort_by:
            sort_column = _check_column(table, sort_by)
            info = table.columns.get(sort_column)
            descending = bool(args.get("sort_desc"))
            if info is not None and info.numeric_style in {"german", "english", "integer"}:
                order = to_numeric_series(selected[sort_column], info.numeric_style)
                selected = selected.loc[
                    order.sort_values(ascending=not descending, na_position="last").index
                ]
            else:
                selected = selected.sort_values(sort_column, ascending=not descending)

        limit = _positive_int(args.get("limit"), default=20, maximum=100)
        return {
            "filter": applied,
            "treffer_gesamt": int(len(selected)),
            "zeilen": selected.head(limit).to_dict(orient="records"),
        }

    raise ValueError(f"Unbekanntes Werkzeug: {name}")


def _trim_text(text: str, max_text_chars: int) -> str:
    """Keep the beginning AND the end of a long document, not just the start.

    A 41-page invoice or catalogue puts the sender, date and document
    numbers at the very beginning and the totals, tax lines, payment terms
    and signatures at the very end -- head-only truncation threw the entire
    second half away, so "what's the total?" on a long document was
    unanswerable from the text even though it's one of the most likely
    questions. The middle is where the repetitive line items live, and
    those are reachable through the table tools and search_text anyway.
    """
    if len(text) <= max_text_chars:
        return text
    head_chars = int(max_text_chars * 0.7)
    tail_chars = max_text_chars - head_chars
    omitted = len(text) - max_text_chars
    return (
        text[:head_chars]
        + f"\n\n…[{omitted} Zeichen aus der Mitte ausgelassen — mit search_text "
        "ist der vollständige Text durchsuchbar]…\n\n"
        + text[-tail_chars:]
    )


def _table_markdown(frame, max_rows: int = 200, max_chars: int | None = None) -> str:
    """Table preview keeping the first and last rows of a long table.

    Same reasoning as _trim_text: a totals or summary row sits at the
    bottom, and head-only preview hid it on any table longer than the cap.
    """
    if max_chars and len(frame):
        # Work out how many rows actually fit in the space this table has
        # been given, from the real width of its own rows.
        sample = frame.head(min(len(frame), 20)).to_markdown(index=False)
        per_row = max(1, len(sample) // max(1, min(len(frame), 20)))
        max_rows = max(4, min(max_rows, max_chars // per_row))
    if len(frame) <= max_rows:
        return frame.to_markdown(index=False)
    head_rows = int(max_rows * 0.7)
    tail_rows = max_rows - head_rows
    omitted = len(frame) - max_rows
    return (
        frame.head(head_rows).to_markdown(index=False)
        + f"\n\n…[{omitted} weitere Zeilen nicht angezeigt — Zählen, Summieren "
        "und Suchen laufen über die Werkzeuge immer über ALLE Zeilen]…\n\n"
        + frame.tail(tail_rows).to_markdown(index=False)
    )


def context_budget(num_ctx: int) -> int:
    """How many characters of context may be built for a given window.

    Ollama does not refuse an over-long prompt -- it silently drops the
    *oldest* tokens to make it fit, and the oldest thing in this
    conversation is the system prompt. So overflowing the window doesn't
    truncate some harmless tail, it deletes the rules that say don't
    guess and use the tools, which is exactly when the model starts
    inventing answers. Measured on a real 50-page catalogue: the context
    came to roughly 20,000 tokens against a 16,384 window.

    Roughly 3.5 characters per token for German/English mixed text, and
    only part of the window is spent on the document -- the rest has to
    hold the system prompt, the question, the tool calls and results, and
    the answer being generated.
    """
    return max(4000, int(num_ctx * 3.5 * 0.55))


def build_context(
    documents: list[Document],
    max_text_chars: int = 40000,
    max_total_chars: int | None = None,
) -> str:
    """Document text, table markdown, and code-computed facts."""
    per_table_chars: int | None = None
    if max_total_chars:
        facts_size = len(json.dumps(compute_facts(documents), ensure_ascii=False, default=str))
        available = max(2000, max_total_chars - facts_size)
        text_share = int(available * 0.45)
        table_share = available - text_share
        max_text_chars = min(
            max_text_chars, max(500, text_share // max(1, len(documents)))
        )
        table_count = sum(len(document.tables) for document in documents)
        per_table_chars = max(500, table_share // max(1, table_count))

    parts = []
    for index, document in enumerate(documents):
        text = _trim_text(document.text or "", max_text_chars)
        marker = (
            " (zuletzt angehängt)" if len(documents) > 1 and index == len(documents) - 1 else ""
        )
        parts.append(f"## Dokument {document.id}: {document.filename}{marker}\n{text}")
        for table in document.tables:
            parts.append(
                f"### {table.id} — {table.label} "
                f"(Spalten: {', '.join(str(c) for c in table.frame.columns)}, "
                f"Zeilen: {table.row_count()})\n"
                + _table_markdown(table.frame, max_chars=per_table_chars)
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
    context = build_context(
        documents,
        max_text_chars=max_text_chars,
        max_total_chars=context_budget(num_ctx),
    )
    messages = [
        {"role": "system", "content": _system_prompt_for(response_language)},
        {
            "role": "user",
            "content": (
                f"DOKUMENTE:\n{context}\n\nFRAGE: {question}\n\n" + _REMINDER
            ),
        },
    ]
    # Not gated on _all_tables(documents): count_text_occurrences works on a
    # document's free text and needs no table at all -- a pure-text upload
    # (no extractable table) must still get tools, not be silently limited
    # to the model's own unreliable reading-based counting.
    tools = TOOL_SCHEMAS if documents else None
    used_tool_names: set[str] = set()
    # A counting question answered without a single tool call is always
    # the model reading the visible extract by eye, which on a long
    # document is wrong by construction. Its text is held back for those
    # questions until it's clear no correction is needed -- otherwise the
    # wrong number would already be on screen before it gets fixed.
    quantitative = needs_a_tool(question)
    nudged = False
    # Once per answer, not once per round -- a corrective round would
    # otherwise print the same "preparing" notice a second time.
    heartbeat_shown = False

    for _ in range(max_rounds):
        content = ""
        tool_calls = None
        seen_output = False
        # Hold back any answer produced before a single tool has run: it
        # is either a self-counted number or an unfounded "it doesn't say",
        # and both get one corrective round below. Releasing it first would
        # put the wrong answer on screen and then argue with it. Once a
        # tool has run, or the correction has been used, streaming is live
        # again as normal.
        withhold = not used_tool_names and not nudged
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
                    if not withhold:
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
            # Claiming the document doesn't say something, without ever
            # having searched it, is unfounded: the visible extract is
            # incomplete on any long document. Found by sweeping real
            # questions -- asked which company issued a 50-page list, it
            # answered "no explicit company name is mentioned" while the
            # name sat in the document text.
            if (
                not nudged
                and claims_absence(content)
                and not (used_tool_names & _SEARCH_TOOLS)
            ):
                nudged = True
                messages.append({"role": "user", "content": _SEARCH_FIRST})
                continue
            if quantitative and not nudged and not used_tool_names:
                # It answered a counting question without calling anything.
                # Measured on a 50-page catalogue: it counted by eye and
                # said 12, then 28, where the real answer was 554 -- and in
                # one run it even wrote the tool call out as prose and
                # invented its result. Ask once, pointedly, for a real call.
                nudged = True
                messages.append({"role": "user", "content": _TOOL_REQUIRED})
                continue
            if withhold:
                yield content  # held back until it was clear no fix was needed
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
