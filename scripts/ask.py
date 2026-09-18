import argparse
import json

from src.agent import answer_question
from src.extractors.router import extract_document


def main():
    parser = argparse.ArgumentParser(
        description="Extract a document and ask a question about it, using a local or remote Ollama model."
    )
    parser.add_argument("file", help="Path to a PDF, CSV, or XLSX file")
    parser.add_argument("question", help="Question to ask about the document")
    parser.add_argument(
        "--model",
        default="qwen2.5:7b",
        help="Ollama model name to use (default: qwen2.5:7b). "
        "Point at a remote Ollama server by setting the OLLAMA_HOST env var "
        "before running this script, e.g. OLLAMA_HOST=http://<mac-studio-ip>:11434",
    )
    args = parser.parse_args()

    extraction = extract_document(args.file)

    print("--- Extraction summary ---")
    print(f"kind: {extraction.kind}")
    print(f"parse_failed: {extraction.parse_failed}")
    if extraction.message:
        print(f"message: {extraction.message}")
    if extraction.facts:
        print(f"facts: {json.dumps(extraction.facts, ensure_ascii=False, indent=2)}")
    if extraction.dataframe is not None:
        print(f"dataframe shape: {extraction.dataframe.shape}")
        print(extraction.dataframe.head(10).to_string())
    print()

    print("--- Answer ---")
    answer = answer_question(args.model, extraction, args.question)
    print(answer)


if __name__ == "__main__":
    main()
