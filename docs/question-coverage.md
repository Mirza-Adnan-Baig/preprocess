# What will people actually ask it?

This is the brainstorm you asked for: not "what's easy to test", but what
a real person at FARO would type when a document is in front of them —
warehouse, sales, purchasing, accounting, management — and whether the
function can answer it today.

Use it as a **test checklist at work**. Every ✅ row should work; every
⚠️ row is a known weak spot; ❌ is not built. When something fails, the
row tells you which tool *should* have run, and the tool-call trace in
the chat tells you which one actually ran — that's usually enough to see
whether it's a tool problem or the model picking the wrong tool.

---

## 1. Counting things

| Question (as a user would type it) | Tool | Status |
|---|---|---|
| "Wie viele Positionen hat die Rechnung?" | `count_rows` | ✅ |
| "Wie viele Zeilen hat die Liste?" | `count_rows` | ✅ |
| "Wie viele Zubehörteile sind in der Liste?" | `count_matching_rows` | ✅ |
| "Wie viele verschiedene Artikel gibt es?" | `column_stats` → `verschiedene_werte` | ✅ |
| "Wie oft kommt das Wort Akku vor?" | `count_text_occurrences` | ✅ |
| "Wie viele Artikel haben Menge größer als 1?" | `query_table` (`op: gt`) | ✅ |
| "Wie viele Artikel kosten über 10 Euro?" | `query_table` (`op: gt`) | ✅ |
| "Wie viele Zeilen haben keinen Barcode?" | `query_table` (`op: empty`) | ✅ |
| "Wie viele Tabellen sind in der Datei?" | `list_tables` | ✅ |
| "Wie viele Seiten hat das PDF?" | `document_info` | ✅ |
| "Wie viele Dateien habe ich hochgeladen?" | `list_documents` | ✅ |

## 2. Adding up / calculating

| Question | Tool | Status |
|---|---|---|
| "Was ist die Gesamtsumme?" | `sum_column` | ✅ |
| "Was kosten alle Zubehörteile zusammen?" | `query_table` (filter + `sum`) | ⚠️ works, but small models often forget the filter — see §9 |
| "Was ist der Durchschnittspreis?" | `query_table` (`avg`) / `column_stats` | ✅ |
| "Was ist der teuerste Artikel?" | `query_table` (`sort_desc`, `limit: 1`) | ✅ |
| "Was ist der billigste Artikel?" | `query_table` (`sort_by`, `limit: 1`) | ✅ |
| "Was sind die 5 teuersten Positionen?" | `query_table` (`sort_desc`, `limit: 5`) | ✅ |
| "Wie viele Stück insgesamt?" (Menge summed) | `sum_column` on Menge | ✅ |
| "Wie viel MwSt ist das?" | text / `search_text` | ⚠️ depends on the document stating it |
| "Was kostet Artikel X mal Menge Y?" | — | ❌ no arbitrary arithmetic between columns |

## 3. Looking something up

| Question | Tool | Status |
|---|---|---|
| "Welchen EAN hat Artikel 33447?" | `find_rows` | ✅ |
| "Zeig mir Zeile 5." | `get_row` | ✅ |
| "Welche Artikel gibt es für iPhone 7?" | `find_rows` | ✅ |
| "Liste alle Artikel über 20 Euro." | `query_table` (filter, no aggregate) | ✅ |
| "Welcher Artikel hat den Barcode 4051805334476?" | `find_rows` | ✅ |
| "Gibt es iPhone 12 Teile in der Liste?" | `find_rows` (empty = no) | ✅ |
| "Gib mir alle EANs." | `query_table` (limit 100) | ⚠️ capped at 100 rows, says so honestly |

## 4. Document facts (header/footer information)

| Question | Tool | Status |
|---|---|---|
| "Wie lautet die Rechnungsnummer?" | text (top of document) | ✅ |
| "Von wann ist die Rechnung?" | text | ✅ |
| "Wer ist der Absender?" | text | ✅ |
| "Wie ist die Kundennummer?" | text | ✅ |
| "Was ist die Zahlungsfrist?" | text / `search_text` | ✅ (now survives long documents, see §7) |
| "Was ist der Gesamtbetrag?" (end of a 41-page doc) | text tail / `search_text` | ✅ fixed — was invisible before |
| "Welche IBAN steht da?" | `search_text` | ✅ |
| "Ist die Rechnung bezahlt?" | text | ⚠️ only if the document says so |

## 5. Data-quality questions (the ones nobody thinks to build, then needs)

| Question | Tool | Status |
|---|---|---|
| "Gibt es doppelte EANs?" | `find_duplicates` | ✅ |
| "Sind Artikelnummern doppelt vergeben?" | `find_duplicates` | ✅ |
| "Fehlt irgendwo ein Barcode?" | `query_table` (`op: empty`) | ✅ |
| "Welche Zeilen sind unvollständig?" | `query_table` (`op: empty`) per column | ✅ |
| "Welcher Wert kommt am häufigsten vor?" | `column_stats` → `haeufigste_werte` | ✅ |
| "Stimmt die Summe mit den Einzelpositionen überein?" | `sum_column` + compare to stated total | ⚠️ model must compare two numbers itself |

## 6. Across several documents

| Question | Tool | Status |
|---|---|---|
| "Wie viele Zeilen haben beide Dateien zusammen?" | `count_rows` (`alle_dokumente`) | ✅ |
| "Welche Artikel sind in beiden Listen?" | — | ❌ no set-comparison tool |
| "Was hat sich zwischen den zwei Versionen geändert?" | — | ❌ no diff tool |
| "Ist Artikel X auch in der zweiten Datei?" | `find_rows` per table | ⚠️ works but needs two calls |

## 7. Long documents (your 41-page case)

The important change. Before, a long document was trimmed to its first
~13 pages and everything after that was simply gone from what the model
could read — so "what's the total?" on page 41 was unanswerable.

Now:
- The visible extract keeps the **beginning and the end** of the document
  (sender/date at the top, totals/payment terms/signature at the bottom),
  with a marker showing the middle was left out.
- Long tables show the **first and last rows** the same way, so a
  totals row at the bottom is visible.
- **`search_text` reads the complete document**, however long, and returns
  the matching passage with its surrounding text. Verified against a
  105,000-character document: it found a passage at character 105,001,
  far past the visible cut-off.
- Every counting/summing tool has always read the complete data — the cap
  only ever affected what the model reads *directly*.

The prompt now tells the model: before claiming something isn't in the
document, search for it first.

## 8. Not supported (know these before you test)

| Question | Why not |
|---|---|
| Anything about a **scanned/photographed** document | No OCR. A photo of an invoice gets rejected or flagged as unreadable — only digital text PDFs, CSV, Excel and text files work. |
| Word (.docx) / PowerPoint (.pptx) | Not wired up. |
| "Which items are in both files?" | No set-comparison tool yet. |
| "Multiply column A by column B" | No arbitrary column arithmetic. |
| Anything needing knowledge outside the document | By design — it answers from the document only. |

## 9. Which tool the model picks — and what turned out not to help

> **Correction, same day, after real office testing:** the "corrective
> round" described below (step 3) was reverted a few hours after this was
> written. It helped the small local model in the measurement table below,
> but on the real office model (`qwen3.6:latest`) it instead caused the
> model to answer with `<tool_code>`/Python-pseudocode blocks — worse than
> the problem it was meant to fix — and doubled generation time on every
> question it triggered on, which made the connection-drop problem worse
> too. The table below is kept as an honest record of what was measured
> and why it looked like a win at the time; steps 1 and 2 are still in
> effect, step 3 is not.

The tools are deterministic and correct. What is *not* guaranteed is the
model choosing the right one and filling it in properly, and a fix for
that turned out to trade one failure mode for a worse one on a bigger
model — worth reading in full before assuming "bigger model = safe to
re-add this."

**What was measured on a generated 50-page, 1,845-row catalogue** (built
by `tools/make_test_document.py`, so the true answers are known) using
the small local model, asking "How many Zubehör articles are in this
list?" — the true answer is **554**:

| Attempt | What happened | Answer |
|---|---|---|
| Before any fix | Called no tool at all, counted the visible extract by eye | **12** ✗ |
| After restating the rule next to the question | Named the right tool but wrote the call as *prose* and invented its result | **28** ✗ |
| After the corrective round | Actually called `count_matching_rows` | **554** ✓ |

Three things make that work, and they matter in this order:

1. **The context is budgeted to fit the window.** Ollama doesn't reject
   an over-long prompt — it silently drops the *oldest* tokens, and the
   oldest thing is the system prompt. On the 50-page document the context
   came to ~20,000 tokens against a 16,384 window, so the rules about not
   guessing were being deleted before the model ever saw them. The
   context now shrinks to fit whatever `NUM_CTX` is set to.
2. **The key rule is repeated right after the question**, not only at the
   top, because on a long document the system prompt is thousands of
   tokens away by the time the model reaches the question.
3. ~~A counting question answered with no tool call triggers one
   corrective round~~ — **reverted**, see the correction note above.

### The measured score, honestly

Twelve questions, all with known answers, run as one batch against the
50-page generated catalogue on the small local model (qwen2.5:7b):

| | Score |
|---|---|
| Before this round of work | **5 / 12** |
| After making `table: "alle"` work on every table tool | 6 / 12 |
| After `mode: starts_with` / `empty` on the counting tool | **8 / 12** |

Two things are worth knowing about that number:

- **Run-to-run variance is large.** Every one of the four remaining
  failures *passed* when the same question was asked on its own. A small
  model is not deterministic, so a single successful test proves much
  less than it feels like it does. Test a few times before concluding
  anything.
- **All four remaining failures are the same thing**: the model writes
  the tool call out as text (*"Let's use the `find_duplicates` tool…"*)
  instead of actually emitting it, then either stops or invents. That is
  a tool-calling *format* weakness of small models specifically, and it
  is the part I cannot fix from the tool layer. Larger models are
  markedly better at it — which is the main reason to expect better
  results on your 27B/35B than these numbers suggest.

**Still verify this at work.** Read the tool-call trace under each answer:

- `count_matching_rows(...)` / `query_table(... op: 'contains' ...)` →
  correct, trust the number.
- `sum_column(...)` with no filter → it summed **everything**, not just
  the group you asked about. The result says so in its own `hinweis`.
- `op: 'equals'` returning 0 with a `hinweis` → the tool computed what
  `contains` would return; the model has to notice and retry on its own
  now (no automatic corrective round anymore — see the correction above).
- **No tool line at all, or `<tool_code>`/pseudocode text instead of an
  answer** → a real, known small/model-quality gap on some models; tell
  me the exact model tag and question if you see it, but this is
  currently a residual, not something the pipe actively fixes.

## 10. Testing without the real file

You can't take the 41-page catalogue home, which made testing anywhere
but at work impossible. Two tools fix that:

**Generate a realistic catalogue with known answers** — same shape as the
real one (article number, description, barcode, EAN, German prices,
quantity, no row-number column), including leading-zero EANs, repeated
EANs and missing barcodes, because all three have broken this pipeline
before:

```bash
python -m tools.make_test_document --seiten 41
```

It writes `test_artikelliste.pdf`, `.csv` and `.xlsx`, then prints the
correct answer to every question you'd ask — computed from the data, not
guessed. Ask the assistant the same questions and compare.

**See what the assistant actually extracts from a real file**, with no
model involved:

```bash
python -m tools.inspect_document "Katalog.pdf" --frage "Zubehör"
```

It prints the page count, every table, every column with how it was
interpreted (number vs. identifier kept as text) and, with `--frage`, the
true hit counts. This answers the question that otherwise costs an hour:
**is a wrong answer an extraction problem or a model problem?** If this
output is right and the answer was wrong, it's the model — look at the
tool-call trace. If this output is already wrong, it's extraction.

Verified on the generated 50-page file: all 1,845 rows merged into one
table across all pages, barcode and EAN kept as exact text (including a
leading zero), German prices parsed, 554 Zubehör rows — matching the
generator's own ground truth exactly.

## 11. Suggested test order at work

1. Short invoice first (2–5 line items) — confirms basics.
2. `document_info` — "Wie viele Seiten hat das Dokument?" on the 41-pager.
3. `search_text` — ask for something you know is on the *last* page.
4. Counting — "Wie viele Positionen?" — check against the document.
5. Filtered sum — "Was kosten alle X zusammen?" — the §9 test.
6. Data quality — "Gibt es doppelte EANs?"
7. Same questions against the CSV/Excel version of similar data.
