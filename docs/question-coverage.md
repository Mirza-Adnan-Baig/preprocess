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
| "Wie viele Zuberhole sind in der Liste?" | `count_matching_rows` | ✅ |
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
| "Was kosten alle Zuberhole zusammen?" | `query_table` (filter + `sum`) | ⚠️ works, but small models often forget the filter — see §9 |
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

## 9. The real remaining weakness: which tool the model picks

The tools are deterministic and correct. What is *not* guaranteed is the
model choosing the right one and filling it in correctly.

Measured on the small local test model (qwen2.5:7b) with "What do all the
Zuberhol parts cost together?":
- It picked `query_table` correctly, but used `op: equals` with a partial
  value ("Zuberhol") against a column containing "Zuberhol Akku Typ 3" —
  zero matches.
- The tool now answers that with an explicit correction: *"no match with
  equals; with contains there would be 12 rows; call again with
  contains; do not invent a number."*
- The 7B model still did not retry, and made a number up anyway.

So: the tool layer does everything it can — it detects the mistake,
computes what the correct call would return, and says so in plain
imperative language. Acting on that correction is the model's job, and a
7B model is simply too weak for it.

**This is the single most important thing to verify at work**, because
your 27B/35B models should handle exactly this much better. Test it with:

> "Was kosten alle Zuberhole zusammen?"

Then read the tool-call trace in the answer:
- `query_table(... op: 'contains' ...)` → correct, trust the number.
- `sum_column(...)` with no filter → it summed **everything**, not just
  Zuberhole. The result now says so in its own `hinweis` field.
- `op: 'equals'` returning 0 with a `hinweis` → watch whether the model
  retries. If it does, the bigger model has solved this. If it invents a
  number anyway, tell me and I'll make the tool layer refuse to answer at
  all rather than let it guess.

## 10. Suggested test order at work

1. Short invoice first (2–5 line items) — confirms basics.
2. `document_info` — "Wie viele Seiten hat das Dokument?" on the 41-pager.
3. `search_text` — ask for something you know is on the *last* page.
4. Counting — "Wie viele Positionen?" — check against the document.
5. Filtered sum — "Was kosten alle X zusammen?" — the §9 test.
6. Data quality — "Gibt es doppelte EANs?"
7. Same questions against the CSV/Excel version of similar data.
