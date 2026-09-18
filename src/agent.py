import json

import ollama

from src.extractors.router import ExtractionResult
from src.tools import TOOL_SCHEMAS, count_rows, sum_column, get_row

TOOL_IMPLEMENTATIONS = {
    "count_rows": count_rows,
    "sum_column": sum_column,
    "get_row": get_row,
}

SYSTEM_PROMPT = (
    "You answer questions about one uploaded business document (an invoice "
    "or inventory list). The document's cleaned content is provided below. "
    "For any question involving counting, summing, or totals, you MUST call "
    "the matching tool rather than counting or adding numbers yourself — "
    "the tools compute exact values from the real data; your own counting "
    "over text is not reliable enough for this task. Respond in German by "
    "default, matching the language of the document and the user, unless "
    "the user's question is written in a different language."
)


NO_TABLE_GUIDANCE = (
    "No structured table was detected in this document — exact counts/sums "
    "are not available; answer only what can be directly read from the text "
    "below, and say so if a count is being requested."
)


def _build_messages(extraction: ExtractionResult, question: str) -> list[dict]:
    context = extraction.markdown or ""
    if extraction.facts:
        context += f"\n\nFACTS: {json.dumps(extraction.facts)}"
    if extraction.dataframe is None:
        context += f"\n\nNOTE: {NO_TABLE_GUIDANCE}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"DOCUMENT:\n{context}\n\nQUESTION: {question}"},
    ]


def answer_question(model: str, extraction: ExtractionResult, question: str) -> str:
    if extraction.parse_failed:
        return extraction.message or "Couldn't parse this document."

    messages = _build_messages(extraction, question)
    df = extraction.dataframe

    for _ in range(4):  # bounded tool-call loop
        response = ollama.chat(model=model, messages=messages, tools=TOOL_SCHEMAS)
        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message["content"]

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            try:
                if name == "count_rows":
                    result = count_rows(df, args.get("filter_expr"))
                elif name == "sum_column":
                    result = sum_column(df, args["column"], args.get("filter_expr"))
                elif name == "get_row":
                    result = get_row(df, args["index"])
                else:
                    result = f"unknown tool: {name}"
            except Exception as exc:
                result = {"error": str(exc)}

            messages.append({
                "role": "tool",
                "content": json.dumps(result),
                "tool_name": name,
            })

    return "Couldn't reach a final answer within the tool-call budget."
