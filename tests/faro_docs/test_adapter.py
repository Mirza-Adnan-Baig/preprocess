import os

from adapters.openwebui_pipe import (
    Pipe,
    collect_files,
    looks_like_openwebui_rag,
    recover_question,
)

RAG_MESSAGE = (
    "### Task:\nRespond to the user query using the provided context, "
    'incorporating inline citations in the format [id] only when the <source> '
    'tag includes an explicit id attribute (e.g., <source id="1">).\n\n'
    "### Guidelines:\n- If you don't know the answer, clearly state that.\n\n"
    "<context>...</context>\n\n<user_query>\nWie viele Zeilen?\n</user_query>"
)


class TestRagDetection:
    def test_detects_open_webui_template(self):
        assert looks_like_openwebui_rag(RAG_MESSAGE)

    def test_plain_question_is_not_flagged(self):
        assert not looks_like_openwebui_rag("Wie viele Zeilen hat die Tabelle?")

    def test_recovers_the_real_question(self):
        assert recover_question(RAG_MESSAGE) == "Wie viele Zeilen?"

    def test_recover_passes_plain_questions_through(self):
        assert recover_question("Wie viele Zeilen?") == "Wie viele Zeilen?"


class TestCollectFiles:
    def test_reads_every_attached_file(self, tmp_path):
        first = tmp_path / "a.csv"
        second = tmp_path / "b.csv"
        first.write_bytes(b"Artikel;Menge\nA;1\n")
        second.write_bytes(b"Artikel;Menge\nB;2\n")
        attached = [
            {"file": {"filename": "a.csv", "path": str(first), "id": "1"}},
            {"file": {"filename": "b.csv", "path": str(second), "id": "2"}},
        ]
        assert len(collect_files(attached)) == 2

    def test_skips_files_that_cannot_be_found(self):
        attached = [{"file": {"filename": "weg.csv", "path": "/nicht/da.csv", "id": "1"}}]
        assert collect_files(attached) == []


class TestPipe:
    def test_asks_for_a_file_when_none_attached(self):
        output = "".join(Pipe().pipe({"messages": [{"content": "Hallo"}]}, __files__=[]))
        assert "Datei" in output

    def test_warns_when_open_webui_rag_is_active(self, tmp_path, monkeypatch):
        path = tmp_path / "a.csv"
        path.write_bytes(b"Artikel;Menge\nA;1\n")
        pipe = Pipe()
        monkeypatch.setattr(
            "adapters.openwebui_pipe.answer", lambda *a, **k: iter(["ok"])
        )
        output = "".join(
            pipe.pipe(
                {"messages": [{"content": RAG_MESSAGE}]},
                __files__=[{"file": {"filename": "a.csv", "path": str(path), "id": "1"}}],
            )
        )
        assert "file_context" in output

    def test_pipe_is_a_sync_generator_not_async(self):
        import inspect

        assert inspect.isgeneratorfunction(Pipe.pipe)
        assert not inspect.isasyncgenfunction(Pipe.pipe)
