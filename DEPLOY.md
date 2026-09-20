# Deploying at the office (Mac Studio)

This is the one document you need to set this up on the real Open WebUI
at FARO. Everything older than this (`START_HERE.md`, `openwebui/README.md`)
was written while this was still being built and is now out of date —
follow this one instead.

## What this actually is, in one paragraph

A single Python file (`openwebui/faro_document_assistant.py`) that you
paste into Open WebUI's admin panel like any other Function. Once active,
it appears as a selectable model in the chat. When someone uploads a PDF,
Excel file, CSV, or text file and asks a question, it reads the real file
directly, extracts any tables in it with real code (not the language
model guessing), and only lets the model answer using either that
extracted data or the document's actual text — never its own arithmetic.

## What you need before starting

You already have all of it — nothing new to install for this step:

- Open WebUI running on the Mac Studio, with admin access.
- Ollama running, with the model you intend to use already pulled
  (`ollama list` should show it).
- That's it. The file has **no pip dependencies** — it only uses
  libraries Open WebUI's own Python environment already ships with
  (pandas, PyMuPDF, openpyxl, etc.). Nothing to install on the Mac Studio
  itself.

**Do you need a vision/OCR model? Not yet.** Scanned documents and photos
aren't handled by this version — see "What this can't do yet" below. If a
future version adds that, it'll need a vision model pulled in Ollama, but
that's not required for anything in this guide.

## Step 1 — Get the file onto the Mac Studio

Either:
- `git clone https://github.com/Mirza-Adnan-Baig/preprocess.git`, or
- Download the repo as a ZIP from that same GitHub page (green **Code**
  button → **Download ZIP**) if Git isn't convenient there.

The one file you actually need is `openwebui/faro_document_assistant.py`.
Open it and copy the whole thing.

## Step 2 — Install it as a Function

1. Open WebUI (as admin) → **Admin Panel → Functions → Create**.
2. Paste the entire contents of `faro_document_assistant.py`.
3. **Save.**
4. Flip the **Active** toggle on.

It'll now show up as **"FARO Dokument-Assistent"** when picking a model in
a new chat.

## Step 3 — One mandatory command, once

Open WebUI has a built-in feature that, unless turned off for this
specific model, secretly rewrites the user's question before this
function ever sees it — and answers a question nobody asked, with no
error shown. This has already caused a real wrong answer once during
testing. Turning it off is one command, run once per Open WebUI install:

```bash
python -m tools.setup_openwebui --url http://localhost:3000 --email <admin-email> --password '<admin-password>'
```

Run this from the folder you cloned/downloaded in Step 1 (needs Python —
if the Mac Studio doesn't have it and you can't install anything there
either, this exact same command can be run from any other machine on the
same network, pointed at the Mac Studio's Open WebUI address instead of
`localhost:3000`).

If you ever see a message starting with **"Achtung: Die eingebaute
Dateiverarbeitung..."** in a chat, this step didn't take effect — re-run
it.

## Step 4 — Configure it for your setup

Click the function's gear icon (Admin Panel → Functions) to see its
settings. Four values, all optional to change:

| Setting | Default | What it means |
|---|---|---|
| `MODEL` | `qwen3.6:27b` | Must exactly match what `ollama list` shows on the Mac Studio. Change this if the model is tagged differently. |
| `OLLAMA_HOST` | *(empty → auto)* | Where to reach Ollama. Leave empty first — it tries a couple of sensible defaults automatically. Only set this yourself if you get a "model not reachable" error (see Troubleshooting). |
| `MAX_TEXT_CHARS` | `40000` | How much of a document's raw text gets sent to the model before it's trimmed (with a visible notice, never silently). Table data and computed counts are never affected by this — only free-text answers on a very long document. |
| `RESPONSE_LANGUAGE` | *(empty → dynamic)* | Leave empty for real use: it answers in whichever language the question was asked in. Set to `en` only for your own testing if you don't want to read German status messages. |

## Step 5 — Try it

New chat → select **FARO Dokument-Assistent** → attach a real invoice or
spreadsheet → ask a question. Try both a counting question ("how many
line items") and a plain question ("who is the sender") to confirm both
work.

## Troubleshooting

**"Das Sprachmodell ist unter `...` nicht erreichbar" / model not
reachable.** Almost always means `OLLAMA_HOST` is wrong, not that Ollama
is actually down. If Open WebUI runs in Docker on the Mac Studio,
`localhost` inside that container is *not* the Mac itself — try
`http://host.docker.internal:11434` as the `OLLAMA_HOST` value. If Open
WebUI is a plain (non-Docker) install, leaving `OLLAMA_HOST` empty should
just work.

**A warning about "eingebaute Dateiverarbeitung" appears in the chat.**
Step 3 wasn't run, or didn't take effect. Re-run it.

**The file was pasted but the model gives an obviously stale/wrong
answer after you update it later.** Before trusting a re-paste, check
there's only one `class Pipe:` in the editor (scroll to the very top and
bottom) — a partial paste in Open WebUI's code editor can silently leave
the old code appended after the new code, and Python quietly uses
whichever `class Pipe:` comes last. If in doubt, delete the function and
re-create it from scratch rather than editing in place.

## What this can do

- Answers exact counting/summing questions using real extracted tables,
  never the model's own arithmetic — the whole reason this project exists.
- Handles PDF, Excel (including every sheet, not just the first), CSV,
  and plain text.
- Handles multiple files uploaded at once.
- Correctly reads German number formats (`1.234,56`), German CSV
  delimiters (`;`), and excludes a "Gesamt" total row from being
  double-counted.
- Answers ordinary questions too — summaries, "who is the sender",
  "what does this say" — directly from the document's real text, not
  just counting questions.
- Says plainly when something isn't in the document, instead of
  inventing an answer.

## What this can't do yet

- **Scanned documents or photos of documents.** Only digital
  (selectable-text) PDFs and native spreadsheet/text files are read
  today. A scanned invoice will get little or no usable text. Fixing
  this needs a vision-capable model pulled in Ollama and some more code
  — not done yet, and not needed for anything above.
- **Word documents (.docx) and PowerPoint (.pptx).** Not wired up yet.
- **One known, narrow accuracy gap**, worth knowing about: on an invoice
  where every line item costs exactly the same amount (e.g. a flat
  hourly rate), there's a small chance (measured at roughly 1 in 25 in
  testing) the last line item gets mistaken for a totals row and left
  out of the count. Every other invoice shape tested — including ones
  with a line-number column or a tax-rate column — is handled correctly.
  This is documented in the code itself
  (`src/faro_docs/tables.py`, function `find_totals_rows`) for whoever
  picks this up next.

## Updating later

If the code changes (new fixes, new formats supported):

```bash
git pull
python -m tools.build_bundle
```

Then repeat Step 2 (delete the old Function, create it fresh with the
newly generated file — don't edit in place, see the troubleshooting note
above about partial pastes). Step 3 only needs to be re-run if you
recreate the function under a different id; otherwise it stays in effect.
