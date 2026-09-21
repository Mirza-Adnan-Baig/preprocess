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

**After this command succeeds, hard-refresh the browser page (Ctrl+R /
Cmd+R) and start a brand-new chat before testing** — reusing a chat/tab
that was already open before this step ran can still show the old,
un-fixed behaviour, because the browser cached the model list from
before the change. Confirmed at a real deployment: the command had
already succeeded, but the very next question — asked in a chat that
was open beforehand — still showed the RAG warning below, until a fresh
chat was started.

If you ever see a message starting with **"Achtung: Die eingebaute
Dateiverarbeitung..."** in a chat, either this step didn't run
successfully, or you need the refresh-and-new-chat step above — re-run
the command, then refresh and start a new chat.

**If the command itself fails with `HTTPError 405`:** check the `--url`
you typed doesn't end in a trailing slash. `http://192.168.1.50:3000/`
(trailing `/`) breaks it; `http://192.168.1.50:3000` (no trailing `/`)
works. As of this version the script strips a trailing slash for you
automatically, so this should no longer happen — if you still see it,
you're running an older copy of the script (`git pull` and try again).

## Step 4 — Configure it for your setup

Admin Panel → **Functions** → find **"FARO Dokument-Assistent"** in the
list → click the small **gear icon** on that row → a **"Valves"** popup
opens listing every setting below, each currently marked **Default** or
**Custom** on the right.

To change one: click the word **Default** next to that setting — it
switches to **Custom** and a text box appears right below it. Type the
new value into that box, then click **Save** at the bottom of the popup.
Five values, all optional to change:

| Setting | Default | What it means |
|---|---|---|
| `MODEL` | `qwen3.6:27b` | Must exactly match what `ollama list` shows on the Mac Studio. Change this if the model is tagged differently. |
| `OLLAMA_HOST` | *(empty → auto)* | Where to reach Ollama. Leave empty first — it tries a couple of sensible defaults automatically. Only set this yourself if you get a "model not reachable" error (see Troubleshooting). |
| `MAX_TEXT_CHARS` | `40000` | How much of a document's raw text gets sent to the model before it's trimmed (with a visible notice, never silently). Table data and computed counts are never affected by this — only free-text answers on a very long document. |
| `NUM_CTX` | `16384` | Ollama's context window, in tokens. **Important:** Ollama silently defaults an unconfigured model to a 4096-token window regardless of what the model itself supports — confirmed directly against a real local model with `ollama ps`. A document with a couple hundred table rows can already exceed that, and Ollama's response to running out of room is to quietly drop the *oldest* part of the prompt, not to show an error — the model then answers confidently from a table it never fully saw. 16384 comfortably covers the default `MAX_TEXT_CHARS`. If you ever raise `MAX_TEXT_CHARS`, raise this too. You can confirm the real value in effect at any time by running `ollama ps` on the Mac Studio right after asking a question — the `CONTEXT` column should read `16384` (or whatever you set), not `4096`. |
| `RESPONSE_LANGUAGE` | *(empty → dynamic)* | Leave empty for real use: it answers in whichever language the question was asked in. Set to `en` only for your own testing if you don't want to read German status messages — see the note about this under "Known limitations" below, since small models don't always honor it. |

## Step 5 — Try it

New chat → select **FARO Dokument-Assistent** → attach a real invoice or
spreadsheet → ask a question. Try both a counting question ("how many
line items") and a plain question ("who is the sender") to confirm both
work.

## Troubleshooting

**"Das Sprachmodell ist unter `...` nicht erreichbar" / model not
reachable.** Almost always means `OLLAMA_HOST` is wrong, not that Ollama
is actually down. If you don't know whether Open WebUI runs in Docker on
the Mac Studio (and can't easily check), try these `OLLAMA_HOST` values
in this order — set each one using the click-path in Step 4, save, start
a new chat, and test again before moving to the next:

1. Leave it empty (**Default**) first — works if Open WebUI is a plain,
   non-Docker install.
2. `http://host.docker.internal:11434` — the fix if Open WebUI runs in
   Docker on the Mac Studio (`localhost` inside that container is *not*
   the Mac itself).
3. `http://<mac-studio-lan-ip>:11434` — the Mac Studio's own network
   address (the same one used to reach Open WebUI itself, e.g. the
   `192.168.x.x` from the browser's address bar), port `11434` instead
   of `3000`.

If none of those work, it's worth asking whoever manages that Mac
directly: is Ollama reachable at `localhost:11434` on it, and is Open
WebUI running in a Docker container?

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
- Never mistakes an ID-like column (EAN/barcode, article number, postal
  code — anything that's just digits, possibly with a leading zero) for a
  quantity. Found on a real product export: a column of EAN codes was
  being silently turned into numbers, stripping the leading zero from
  every code that had one (`0107610691403` became `107610691403` — a
  different, wrong code). Fixed at the root: any column with a leading
  zero anywhere in it is now always kept as exact text.
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

## Known limitations

Everything in this section was found by actually testing against real
files (a real payslip, a real 173-row CSV export, a real 297-row product
list, real academic PDFs, a real university certificate) and a real local
Ollama model, not synthetic test fixtures. Each item below is labelled as
either a fixed code bug or an open model-quality gap, so it's clear which
ones a bigger production model should resolve on its own and which ones
were actually fixed in this code.

**Fixed at the code level, this round:**

- Open WebUI hands the pipe *every file ever attached in a chat thread* on
  every turn, with no signal telling "just attached" apart from "attached
  several messages ago." A plain question like "how many rows" now
  correctly scopes to only the most recently attached document — verified
  via the tool-call trace shown in the chat (`count_rows({'table': 'alle'})`,
  the number next to the arrow is always correct).
- ID-like numeric columns (see "What this can do" above) are no longer
  silently corrupted by being treated as numbers.
- A document large enough to exceed Ollama's real default context window
  (4096 tokens — see the `NUM_CTX` row above) no longer gets silently
  truncated mid-table before the model ever sees it.

**Open model-quality gaps — expected to improve with the real production
model, not fixable by more code:**

- **Specific-value lookups on a table with a couple hundred rows or more**
  are unreliable on a small model even though the correct value is
  genuinely present in what it's shown, and even though a `get_row`/
  `find_rows` tool exists specifically for this (the prompt now
  explicitly tells it to use them). Tested directly against a real
  297-row product list on the local 7B test model: it either claimed the
  value wasn't available, or invented a plausible-looking wrong one,
  every time. Broad questions ("what's this document about", "how many
  rows total") and directly-quotable facts near the top of a document
  (an ID number, a date, a short table) were answered correctly and
  consistently in the same tests. Re-verify specifically the
  large-table-row-lookup case against the real production model before
  trusting it there.
- **`RESPONSE_LANGUAGE=en` (testing only) isn't always honored** when the
  source document itself is in German — the small local test model
  sometimes answers in German anyway despite an explicit, repeated
  instruction not to. The *data* in the answer was correct both times
  this was tested; only the language of the sentence around it was wrong.
  Not expected to be a real-world problem: the actual office default
  (`RESPONSE_LANGUAGE=""`, matching whatever language the question itself
  was asked in) doesn't depend on overriding the document's own language.
- **A small model can still mention a stale cross-document aggregate**
  in its written sentence even when the underlying tool call it made was
  correctly scoped (confirmed: identical repeated test, one run mentioned
  it, the next didn't). If an answer after several file uploads in one
  chat looks combined or stale, check the tool-call trace line for the
  real number, or start a new chat per document to be certain.
- One narrow, quantified totals-row edge case remains: on an invoice
  where every line item costs exactly the same amount, there's roughly a
  1-in-25 chance the last line item is mistaken for a totals row (see
  "What this can't do yet" below).

**One operational tip, not a bug:** Open WebUI's own automatic chat-title,
tag, and follow-up-question generation (Admin Panel → Settings → Interface)
run extra requests against the *same* Ollama model right around when it's
also answering the real question. If GPU/RAM is tight on the Mac Studio,
turning those three off keeps the model's full attention on the actual
document instead of context-switching between unrelated requests.
