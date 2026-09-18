# Start here

You were asleep while this got built. This document assumes you know
nothing about what happened — read it top to bottom and you'll have the
full picture. Everything referenced here is inside this repo; you don't
need anything else.

## 1. The one-paragraph version

Your company (FARO) wants a local, air-gapped LLM (Qwen3.6-27B, running on
the Mac Studio via Ollama + Open WebUI) to answer questions about business
documents (invoices, inventory lists — all in German). The problem: asking
"how many articles are in this document" on a long PDF or messy spreadsheet
gave a different wrong answer every time, because the model was either
guessing from a partial view of the document (RAG chunking) or literally
counting rows in text, which LLMs are bad at. This repo is a working
prototype — built and tested overnight on Mirza's personal PC, not the
Mac Studio — that fixes this by never letting the LLM count anything
itself. It extracts the document into a real, structured table with actual
code (pandas), and gives the LLM a *tool* it can call to get the exact
count back. **This is confirmed working end-to-end against a real local
model, not just unit tests** — see §4.

## 2. How it works

```
your file → extract_document()  → routes by extension
                                     ├─ .csv/.xlsx → pandas: finds the real header row (skips
                                     │                title/blank rows), cleans it, builds an exact
                                     │                FACTS block (row count, sums, etc.) — computed
                                     │                by code, never guessed
                                     └─ .pdf        → PyMuPDF reads text + detects tables page by
                                                       page, merges table fragments that span
                                                       multiple pages, OCRs scanned pages in German
                                                       if there's no extractable text
                                     ↓
                        one clean "DOCUMENT: ..." block + a real pandas table in memory
                                     ↓
                       sent to the model (Qwen) along with 3 tools it can call:
                       count_rows / sum_column / get_row — each backed by that real table
                                     ↓
              "how many articles?" → model calls count_rows() → gets the actual number → answers
```

The model never counts by reading text. It calls a tool, the tool runs
real pandas code against the real extracted table, and the model just
reports whatever number came back. That's the whole idea.

## 3. Where everything is in this repo

```
src/
  extractors/
    tabular.py    — CSV/XLSX: header detection, cleaning, exact FACTS block
    pdf.py        — PDF: text + table extraction, German OCR fallback
    router.py     — picks the right extractor by file extension; builds a
                     DataFrame from PDF tables too (including multi-page ones)
    dtypes.py     — makes extracted columns actually numeric so filters work
  tools.py        — count_rows / sum_column / get_row, the exact-answer tools
  agent.py        — talks to Ollama, runs the tool-calling loop, builds the
                     German-by-default system prompt
scripts/
  generate_synthetic_data.py — makes fake invoices/inventory for testing
  ask.py          — the command you actually run to test a real file (§6)
tests/            — 31 tests, all passing, covering every module above
docs/
  design-spec.md  — the full design document (read this for the "why")
  phase0-plan.md  — the step-by-step plan this was built from
README.md          — shorter technical reference (setup, limitations)
START_HERE.md       — this file
```

## 4. What happened overnight (the honest version)

The original plan had real bugs in it, and the process found and fixed all
of them through code review before anything was called "done":

1. **CSV parsing crashed** on messy files (a short title row followed by
   wider data rows) — fixed to tolerate ragged rows.
2. **You told me mid-build that all documents and users are German.** This
   changed the design: OCR now uses the German language pack (`deu+eng`),
   and the model is instructed to answer in German by default.
3. **`sum_column` had a silent-wrong-answer bug** — it returned a fake
   `0.0` for a non-numeric column instead of raising an error. Fixed to
   raise clearly instead.
4. **The big one: PDFs never actually got exact counts.** The original
   plan built the tool-calling mechanism but never connected PDF tables to
   it — meaning your original 41-page-PDF problem would *not* have
   actually been fixed by this prototype as first built. This was caught
   during review (not by you asking) and fixed: PDF tables now become a
   real data table too, including ones that span multiple pages.
5. **I installed Ollama and a small model (`qwen2.5:7b`) on the dev PC and
   ran the full pipeline for real**, instead of leaving the validation
   test skipped. That surfaced three more real bugs: a crash on corrupt
   files instead of a clean error message, numeric filters failing on
   CSV/PDF-sourced data (only spreadsheets worked), and a wrong field name
   that would have broken multi-part questions. All fixed and re-verified.

Every one of these was found by an independent review step before being
marked complete — nothing here is "probably fine," it was checked.

**Final result: 31/31 tests passing**, and the actual end-to-end question
"how many articles are on this invoice?" answered correctly by a real
local model calling the real tool against a real (synthetic) invoice.

## 5. Setting up on a fresh PC (from absolute zero)

This assumes the machine has nothing installed. If something below is
already there, skip that step.

### 5.1 Install Git

Download and install from https://git-scm.com/downloads (or, faster, open
PowerShell and run):

```powershell
winget install --id Git.Git -e --source winget
```

Close and reopen your terminal after installing so `git` is on your PATH.

### 5.2 Install Python (3.11 or newer)

```powershell
winget install --id Python.Python.3.12 -e --source winget
```

Close and reopen your terminal after installing. Confirm it worked:

```powershell
python --version
```

### 5.3 Get the code onto the machine

**If Git is installed:**

```powershell
git clone https://github.com/Mirza-Adnan-Baig/preprocess.git
cd preprocess
```

**If Git is NOT installed and you don't want to install it** (e.g. you
only have Python + VS Code and no admin rights to add Git): download the
code as a ZIP instead — no Git required.

1. Go to https://github.com/Mirza-Adnan-Baig/preprocess in a browser.
2. Click the green **Code** button → **Download ZIP**.
3. Extract the ZIP anywhere (e.g. `Documents\preprocess`).
4. Open that extracted folder in VS Code (**File → Open Folder**), and use
   VS Code's built-in terminal (**Terminal → New Terminal**) for every
   command below instead of PowerShell — it's the same thing, just inside
   the editor.

The only downside of the ZIP route: you won't get future updates
automatically (you'd re-download the ZIP), and you can't `git push` any
changes back. Fine for just running and testing the pipeline.

### 5.4 Create a virtual environment and install dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

You'll need to run `.venv\Scripts\activate` again every time you open a
new terminal to work on this.

### 5.5 Install Tesseract OCR (only needed for scanned/image PDFs)

Download the Windows installer from
https://github.com/UB-Mannheim/tesseract/wiki (this is the standard
Windows build). **During installation, make sure the German language pack
is included** — the installer has a checkbox for additional languages; if
you miss it, or you're not sure, you can add it after: download
`deu.traineddata` from
https://github.com/tesseract-ocr/tessdata and place it in Tesseract's
`tessdata` folder (typically
`C:\Program Files\Tesseract-OCR\tessdata\`).

If you're only testing with digital (non-scanned) PDFs or spreadsheets
today, you can skip this — it's only used as a fallback for image-only PDF
pages.

### 5.6 Run the test suite to confirm everything works

```powershell
pytest -v
```

You should see `31 passed`. (One test needs a local Ollama model — see §5.7
— it will `SKIP` cleanly if Ollama isn't installed, which is fine.)

### 5.7 (Optional) Install Ollama for local testing

Only needed if you want to run the full model in the loop *on this
machine* rather than pointing at the Mac Studio (see §7 for the remote
option, which is what you'll likely want at the office).

```powershell
winget install --id Ollama.Ollama -e
ollama pull qwen2.5:7b
```

## 6. Testing against a real document — the command you actually want

Once set up (§5.1–5.4 minimum), run:

```powershell
python -m scripts.ask path\to\your\invoice.pdf "How many articles are on this invoice?"
```

(Run it as `python -m scripts.ask`, not `python scripts\ask.py` — the
former finds the project's code correctly, the latter won't.)

It prints two things:
1. **The extraction summary** — what got pulled out of the document (row
   count, columns, the actual table if one was built). This is useful on
   its own even without a model: it tells you immediately whether the
   parser handled your real file correctly.
2. **The answer** — what the model said, after (hopefully) calling the
   right tool to get an exact number.

Try it with a CSV or XLSX too:

```powershell
python -m scripts.ask path\to\inventory.xlsx "What's the total quantity where unit price is above 50?"
```

## 7. Testing at the office (weak PC, real invoices, no local model)

Your office PC doesn't need to run the LLM itself. The extraction part
(reading the file, building the table) is lightweight and works fine on
any PC — the only heavy part is the model, and that can run remotely on
the Mac Studio over your office LAN instead of locally.

1. Do §5.1–5.4 above (git, Python, clone, install deps) — skip §5.7
   (installing Ollama locally), you're borrowing the Mac Studio's instead.
2. **Ask your Head of IT this specific question today:** is the Mac
   Studio's Ollama server reachable over the LAN from other machines, or
   only from Open WebUI running on that same machine? These are different
   things — Open WebUI reaching Ollama over `localhost` doesn't prove
   anything about LAN reachability. If it's not currently open, ask what
   it would take to open it (typically: setting `OLLAMA_HOST=0.0.0.0` on
   the Mac Studio's Ollama service, plus a firewall allowance for port
   `11434`).
3. **If reachable:**
   ```powershell
   set OLLAMA_HOST=http://<mac-studio-lan-ip>:11434
   python -m scripts.ask path\to\real_invoice.pdf "How many articles are in this document?" --model qwen3.6:27b
   ```
   (Check the exact model name/tag with `ollama list` on the Mac Studio if
   `qwen3.6:27b` doesn't match — IT can confirm.)
4. **If not reachable (yet):** you can still validate the more
   failure-prone half of this — whether a real messy German invoice or
   inventory file actually parses correctly — because the extraction
   summary (point 1 in §6) prints *before* any model call happens. Run the
   command anyway; even if the final model call fails or times out because
   there's no reachable server, you'll already see the extracted table and
   know whether the parser worked on your real document.

## 8. Known limitations — be honest about these with IT

- **PDF tables spanning multiple pages only merge correctly if each page
  repeats the header row.** This works for every test fixture and most
  real invoicing software, but hasn't been verified against a real
  multi-page FARO invoice yet — do that first with a real document.
- **A PDF with no detectable table gives no exact counts at all** — the
  model is told this explicitly rather than guessing, but you'll just get
  a qualitative answer from the text in that case.
- **No `.docx` support yet** — the design calls for it, Phase 0 doesn't
  implement it.
- **The tool-calling filter isn't sanitized against malicious input.**
  This is fine for a single-user local prototype (the only thing that can
  ever supply that input is the LLM itself, running locally, with no
  external network access), but **this must be hardened before real
  employee documents run on the shared Mac Studio** (Phase 2).
- **The German OCR fix is code-correct but was never tested against a
  real scanned document** on the dev machine (no scanner/scanned PDF was
  available). Test this with an actual scanned invoice before trusting it.

## 9. What's next

1. Test this against 2-3 real (or anonymized) FARO documents — an invoice
   PDF and an inventory XLSX/CSV at minimum. This is the most valuable
   thing you can do next; everything so far was validated against
   synthetic data.
2. Have the IT conversation from §7.2 above.
3. Once real-document testing looks good, the next phase is packaging this
   as an Open WebUI Pipeline (a plugin) so it runs automatically when
   *anyone* uploads a file in the chat — not just when you run this script
   manually. See `docs/design-spec.md` §8 for the full rollout plan.
4. Before Phase 2 (real employee use, not just your own testing): harden
   the filter-input handling noted in §8.

## 10. If something doesn't work

- `ModuleNotFoundError: No module named 'src'` — you ran
  `python scripts\ask.py` instead of `python -m scripts.ask`. Use the
  latter.
- `TesseractError` on a scanned PDF — the German (`deu`) language pack
  isn't installed; see §5.5.
- Tests fail entirely (not just the Ollama one skipping) — make sure you
  activated the virtual environment (`.venv\Scripts\activate`) and ran
  `pip install -r requirements.txt` first.
- The `test_agent_e2e.py` test SKIPs — that's fine and expected if Ollama
  isn't installed on this machine (see §5.7 or §7).
