"""Ask a question about a document from the command line.

Usage: python -m scripts.ask <datei> [<datei> ...] "<Frage>"
"""

import sys

from src.faro_docs.answer import answer
from src.faro_docs.ingest.router import ingest_all


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(1)

    *paths, question = sys.argv[1:]
    files = []
    for path in paths:
        with open(path, "rb") as handle:
            files.append((path.rsplit("/", 1)[-1], handle.read()))

    documents = ingest_all(files)
    for chunk in answer(
        documents, question, model="qwen2.5:7b", host="http://localhost:11434"
    ):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    main()
