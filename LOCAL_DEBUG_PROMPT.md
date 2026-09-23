# Debugging prompt — paste this into your local LLM chat

This file is written to be **self-contained**: paste the whole thing into a
chat with your local `qwen3.6:35b` (or any other local model) that has no
other context about this project, and it should have everything it needs
to help you find the bug. Paste `src/faro_docs/answer.py` and
`adapters/openwebui_pipe.py` from this repo alongside it (they're the two
files most relevant to the bug) — this document tells the model what to
look for in them.

---

## The situation

I (Mirza) work at **FARO**, a German company that imports and exports
phone parts. I'm building a local, air-gapped document-question-answering
tool for the office, because a generic LLM given a long document would
hallucinate wrong counts and totals when asked things like "how many line
items are on this invoice."

**The hardware:** a Mac Studio (M2 Ultra, 64GB RAM) at the office, running
**Ollama** (serving local models) and **Open WebUI** (the chat interface,
admin-configured through its web UI, no shell access to that machine for
me — only the Head of IT has that).

**How the tool is built:** it's a single Python file — an Open WebUI
**"Pipe Function"** — that you paste into Open WebUI's Admin Panel. Once
active, it shows up as a selectable model in the chat. When a user
attaches a file and asks a question, this function:

1. Reads the real file directly (PDF via PyMuPDF, or CSV/Excel via
   pandas) and extracts any tables with **real code**, not by asking the
   model to read and guess.
2. Sends the model the document's text/tables plus a list of **tools**
   (functions) it can call to get exact answers — counting rows, summing
   a column, searching text, etc. — instead of letting the model count or
   add numbers itself, which is unreliable.
3. The model is expected to call one of these tools (using Ollama's
   native tool-calling / function-calling mechanism) when the question
   needs one, read the tool's result, and then answer based on that.

**The documents:** real German business documents — mostly PDF price
lists / parts catalogs. The realistic shape: a table with columns like
`Artikelnr` (article number), `Bezeichnung` (description), `Barcode`,
`EAN` (a 13-digit product code), `Einzelpreis` (unit price, German decimal
format like `4,86`), `Menge` (quantity) — **no row-number column**. A real
test document is 41 pages long, with the same table continuing across
every page (same header repeated per page). Also tested against
CSV/Excel exports of similar data.

**The models available:** `qwen2.5:7b` (a small local test model, works
correctly with tool-calling — confirmed via extensive testing) and
`qwen3.6:35b` (the model currently being tested at the office, MoE
architecture) — **this is the one that's broken.**

## The exact bug

Asking a question that needs no tool at all, e.g. **"what is this
document?"**, the entire chat response was just:

```
document_info(dokuments="dok1")
```

Or, on a different question, similar broken output:

```
<tool_code>
print(document_info(dok1))
```

**That's the entire response — no actual answer, just that literal text.**

This is Python-pseudocode-style text that *looks like* a function call,
but it is NOT a real, structured tool call that Ollama's API parsed. If it
were, the code in `answer.py` would see it in
`item["message"]["tool_calls"]`, actually execute the real `document_info`
Python function, and continue the conversation with its result. Instead,
this text arrived as plain `message.content` — meaning **Ollama never
recognized it as a tool call at all**; the model just wrote what it thinks
a tool call looks like, as ordinary text.

## What's already been ruled out

- **Not a stale-deployment issue.** Confirmed the exact latest code is
  what's deployed.
- **Not caused by an over-aggressive "call a tool now" prompt trick.** An
  earlier fix attempt added a rule that told the model "you didn't call a
  tool, do it now" whenever this happened. It was removed entirely
  (confirmed via git history) and **the exact same pseudocode-output bug
  still happens** — so that prompt text was not the root cause, or not the
  whole story.
- **Not a document-parsing problem.** This happens even on a basic
  question needing no document data at all.

## What needs to be figured out

The leading hypothesis: **`qwen3.6:35b`'s specific Ollama package may not
have a properly-implemented tool-calling chat template**, so it has no
reliable, trained way to emit Ollama's native tool-call format — it's
falling back to imitating "what code that calls a function looks like"
from its general training, in whatever style it happens to reach for
(Python `print()`, `<tool_code>` blocks, etc.), rather than a real,
parseable call.

**The diagnostic that would confirm or rule this out:**

```
ollama show qwen3.6:35b --modelfile
```

Look at the `TEMPLATE` section. A model with real tool-calling support has
a block like this (confirmed present and working correctly on
`qwen2.5:7b`'s template):

```
{{- if .Tools }}

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{{- range .Tools }}
{"type": "function", "function": {{ .Function }}}
{{- end }}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>
{{- end }}
```

**Questions for you (the local LLM) to help reason through, once you've
seen `qwen3.6:35b`'s actual TEMPLATE output:**

1. Does its `TEMPLATE` have an equivalent `{{ if .Tools }}` block at all?
2. If yes — does it use the same `<tool_call>{"name": ..., "arguments": ...}</tool_call>` JSON convention, or a different one (e.g. a Python/code-style convention)? If different, is there a way to tell from the Modelfile whether Ollama's own tool-call parser (which extracts `message.tool_calls` from the raw output) actually understands that convention, or only the `<tool_call>` JSON one?
3. If the template is missing or uses an unrecognized convention: is there a *different* Ollama tag/quantization of the same Qwen 3.6 35B model that *does* ship a working tool-calling template? (Community-uploaded GGUF conversions on Ollama's library sometimes have broken or stripped chat templates compared to the official one.)
4. Is there a way to override/patch the Modelfile's `TEMPLATE` locally (`ollama create` with a custom Modelfile based on the same weights but a corrected template) as a workaround, without needing to find a different upload of the model?

## The relevant code (for reference — read the actual files too)

The part of `src/faro_docs/answer.py` that matters most is the function
`answer()` — specifically this loop, which sends `tools=TOOL_SCHEMAS` to
Ollama and checks for a real structured tool call:

```python
for item in _stream_with_heartbeat(client, model, messages, tools, ...):
    ...
    piece = item.get("message", {}).get("content", "")
    if piece:
        content += piece
        yield piece
    if item.get("message", {}).get("tool_calls"):
        tool_calls = item["message"]["tool_calls"]
...
if not tool_calls:
    # no real tool call happened -- just finish with whatever text content
    # the model produced (this is the branch that's firing when it
    # shouldn't: the model's pseudocode ends up here as if it were a
    # normal, tool-free answer)
    ...
    return
```

`tools` is built from `TOOL_SCHEMAS`, a list of OpenAI-style JSON function
schemas (name, description, parameters) — the same format `qwen2.5:7b`
correctly understands and calls.

## What a fix would look like, roughly

Two different directions, depending on what the `ollama show` diagnostic
reveals:

- **If the template is genuinely broken/missing:** the fix is choosing a
  different, correctly-packaged model (or a corrected custom Modelfile),
  not a code change in this project.
- **If the template exists but represents tool calls differently than
  `qwen2.5:7b`'s:** it might be possible to add a fallback parser in
  `answer.py` that detects this model's specific pseudocode pattern (e.g.
  a regex for `<tool_code>\s*print\((\w+)\((.*)\)\)` or
  `(\w+)\((\w+)=["'](.*)["']\)`) in the plain-text `content` when
  `tool_calls` comes back empty, and manually converts it into a real
  call to `run_tool(...)` — effectively translating the model's own
  broken output into a working one. This is a legitimate, if inelegant,
  workaround if this model's own tool-calling truly can't be fixed at the
  Ollama/template level.

If you find a fix or a strong diagnosis of the root cause, take notes so I
can share the outcome and adjust the actual project code if a code-level
fix (like the fallback parser above) turns out to be the right approach.
