"""End-to-end streaming behaviour of answer(), against a fake Ollama client.

Covers the heartbeat UX: Ollama's prefill on a large document can take long
enough that several heartbeat ticks fire before the first real token -- the
visible "still working" notice must appear at most once per wait, not once
per tick, with any further ticks reduced to an invisible keep-alive so the
connection still sees regular bytes.
"""

import inspect
import time

import pandas as pd
import pytest

from src.faro_docs.answer import DENKT_NACH, DENKT_NACH_EN, _KEEPALIVE, answer
from src.faro_docs.model import ColumnInfo, Document, Table


def test_default_heartbeat_interval_is_short():
    """Open WebUI (functions.py) iterates this generator with a plain
    synchronous `for` loop inside an async function -- confirmed by reading
    its source -- so every single poll genuinely blocks its *entire* event
    loop, not just this chat's request, for up to heartbeat_interval
    seconds. A long default here reopened exactly the "connection lost,
    reconnecting" symptom this project exists to avoid, confirmed live at
    a real office deployment. Keep it short."""
    default = inspect.signature(answer).parameters["heartbeat_interval"].default
    assert default <= 1.0


def _document():
    frame = pd.DataFrame({"Artikel": ["A", "B"], "Menge": [1.0, 2.0]})
    return [
        Document(
            id="dok1", filename="a.csv", media_type="text/csv", text="FARO GmbH",
            tables=[Table(id="dok1:t1", label="Tabelle 1", frame=frame,
                          columns={"Menge": ColumnInfo("Menge", "german", "", True)})],
        )
    ]


class _FakeClient:
    """Mimics ollama.Client: .chat(..., stream=True) returns an iterator of
    chunks, after an initial delay long enough to trigger several heartbeat
    ticks at the test's tiny interval."""

    def __init__(self, delay: float, chunks: list[dict]):
        self.delay = delay
        self.chunks = chunks

    def chat(self, model, messages, tools, stream, options=None):
        self.last_options = options
        self.last_tools = tools
        time.sleep(self.delay)
        yield from self.chunks


def _install_fake_client(monkeypatch, client):
    import ollama

    monkeypatch.setattr(ollama, "Client", lambda host: client)


def test_heartbeat_notice_shown_once_not_once_per_tick(monkeypatch):
    """A slow prefill (several heartbeat ticks) must not stack up several
    visible '(still working ...)' lines -- exactly one, ever, per wait."""
    client = _FakeClient(
        delay=0.25,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=0.05,
        )
    )
    text = "".join(chunks)

    assert text.count(DENKT_NACH) == 1
    assert DENKT_NACH_EN not in text
    assert "Antwort." in text


def test_extra_heartbeat_ticks_are_invisible_keepalive(monkeypatch):
    """Ticks after the first notice must still put bytes on the wire (so the
    connection doesn't look idle) but render as nothing visible."""
    client = _FakeClient(
        delay=0.3,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=0.05,
        )
    )

    assert any(chunk == _KEEPALIVE for chunk in chunks)
    assert _KEEPALIVE == "​"  # zero-width space: real bytes, renders as nothing


def test_no_heartbeat_when_the_model_answers_immediately(monkeypatch):
    """A fast answer (no wait longer than one interval) never shows the
    notice at all -- it exists only to cover genuinely long prefills."""
    client = _FakeClient(
        delay=0.0,
        chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "Was steht im Dokument?", model="m", host="h",
            heartbeat_interval=1.0,
        )
    )
    text = "".join(chunks)

    assert DENKT_NACH not in text


def test_tools_are_offered_even_for_a_document_with_no_tables(monkeypatch):
    """count_text_occurrences needs no table -- a plain-text upload with
    nothing extractable as a table must still get tools, not be silently
    limited to the model's own unreliable reading-based counting (this
    used to be gated on _all_tables(documents), which was empty/falsy for
    a table-less document)."""
    text_only = [
        Document(id="dok1", filename="a.txt", media_type="text/plain", text="hallo welt")
    ]
    client = _FakeClient(
        delay=0.0, chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    list(answer(text_only, "Frage?", model="m", host="h"))

    assert client.last_tools is not None
    assert any(t["function"]["name"] == "count_text_occurrences" for t in client.last_tools)


class _RoundAwareFakeClient:
    """Returns a different canned response on each successive .chat() call,
    to simulate a tool-call round followed by the model's final answer."""

    def __init__(self, rounds: list[list[dict]]):
        self.rounds = rounds
        self.call_count = 0

    def chat(self, model, messages, tools, stream, options=None):
        chunks = self.rounds[self.call_count]
        self.call_count += 1
        yield from chunks


def test_footer_says_text_search_not_table_when_only_that_tool_was_used(monkeypatch):
    """count_text_occurrences answers a question with no table involved at
    all -- the footer must not claim 'computed from the table'."""
    client = _RoundAwareFakeClient([
        [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "count_text_occurrences",
                          "arguments": {"search": "GmbH", "document": "alle"}}}
        ]}}],
        [{"message": {"content": "It appears once.", "tool_calls": None}}],
    ])
    _install_fake_client(monkeypatch, client)

    text = "".join(answer(
        _document(), "How often does GmbH appear?", model="m", host="h",
        response_language="en",
    ))

    assert "text search" in text.lower()
    assert "computed from the table" not in text.lower()


def test_num_ctx_is_always_passed_to_ollama(monkeypatch):
    """Ollama silently defaults to a 4096-token window regardless of what the
    model supports (confirmed via `ollama ps` against a real local model) --
    a real multi-hundred-row table plus its FAKTEN block can exceed that,
    and Ollama quietly truncates the *oldest* part of the prompt to fit
    rather than erroring, so the model confidently answers from a table it
    never fully saw. num_ctx must be set explicitly on every call, not left
    to Ollama's default."""
    client = _FakeClient(
        delay=0.0, chunks=[{"message": {"content": "Antwort.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    list(answer(_document(), "Frage?", model="m", host="h", num_ctx=16384))

    assert client.last_options == {"num_ctx": 16384}


def test_response_language_en_uses_english_notice(monkeypatch):
    client = _FakeClient(
        delay=0.25,
        chunks=[{"message": {"content": "Answer.", "tool_calls": None}}],
    )
    _install_fake_client(monkeypatch, client)

    chunks = list(
        answer(
            _document(), "What is in the document?", model="m", host="h",
            response_language="en", heartbeat_interval=0.05,
        )
    )
    text = "".join(chunks)

    assert text.count(DENKT_NACH_EN) == 1
    assert DENKT_NACH not in text
