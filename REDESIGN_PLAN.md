# Redesign plan — stepping back from the tool-per-function approach

Written 2026-09-23. **Nothing in this file has been implemented yet.** This
is the plan to discuss and approve before any code changes.

## Why we're changing course

The approach so far — one Open WebUI Pipe Function with a growing set of
bespoke Python tools (13 by the end) — hit a real ceiling tonight:

- Every tool added its own JSON schema to *every single request*, and that
  fixed cost nearly tripled across tonight's session (measured: ~1,325
  tokens → ~3,536 tokens of schema+prompt overhead, before a single
  character of the actual document). That eats directly into the same
  context window the document needs.
- The model has to pick the *exact right one* of 13 similarly-shaped
  tools and fill in the *exact right argument names* every time. Small
  mistakes (wrong table id, wrong operator, wrong column name) were
  common and each needed its own hand-written correction.
- On the real office model, a basic "summarize the document" request —
  needing no tool at all — hung, kept loading, and eventually
  disconnected without ever answering.

Continuing to patch this tool-by-tool is not worth the effort for the
result it's producing. This plan starts from the actual data-handling
problem again, not from the Pipe-Function shape.

## What's staying

The document *extraction* layer is solid and well-tested (220+ tests) and
is not part of the problem — keep it as-is:

- PDF table detection and cross-page merging (PyMuPDF)
- CSV/Excel ingestion (pandas)
- German number-format detection (`src/faro_docs/german.py`) — decimal
  comma vs point, and the identifier-vs-quantity logic (leading zeros,
  long unique digit codes) that took real debugging to get right tonight
- The "keep every file, don't confuse old and new uploads" logic

None of this depended on the tool-calling shape and none of it is being
thrown away.

## Step 1 — Verify the foundation first, before any redesign

This has to happen first, because if the infrastructure itself is
misconfigured, no amount of architecture work fixes it. **Do this on the
Mac Studio as soon as you have access, before touching any code.**

### Is Ollama running natively or inside Docker?

```bash
# Lists running containers if Docker is involved at all
docker ps

# Shows which process is actually bound to Ollama's port
lsof -i :11434

# If native, macOS runs Ollama as a background service/menu-bar app
launchctl list | grep -i ollama
ls -la /Applications/Ollama.app 2>/dev/null && echo "native app is installed"
```

If `docker ps` shows an `ollama` container, or `lsof` shows the port owned
by `com.docker.backend` / `docker-proxy` rather than an `ollama` binary
directly, it's running in Docker.

### Is Open WebUI running natively or inside Docker?

```bash
docker ps                    # look for an open-webui container
lsof -i :3000                # or whatever port it's on
ps aux | grep -i open-webui  # native: shows a normal python/venv process
```

### Why this matters — the pros and cons, specifically for this hardware

**This is the most important thing to check, and it may explain the
disconnecting/hanging on its own**, independent of anything in our code:

| | Native (installed directly on macOS) | Docker |
|---|---|---|
| GPU/Metal access | Full access to the M2 Ultra's GPU cores | **Docker Desktop for Mac runs containers inside a Linux VM. That VM has historically not had full Metal/GPU passthrough** — a model that should run on GPU can silently fall back to CPU-only or badly degraded performance inside a container. This alone could explain long waits and disconnects that look like a code bug but aren't. |
| Ollama's MLX backend | Since Ollama v0.19, the native macOS build uses Apple's own MLX framework for a real speed boost on M-series chips | A Linux container almost certainly does not get this Apple-specific optimization at all |
| Available RAM | Full 64GB available to the process | Docker Desktop has its own configurable memory limit for the VM, often set well below the host's total RAM by default — a 35B model plus a large context window could be memory-starved without it being obvious why |
| Networking (`OLLAMA_HOST`) | Simple — `localhost` usually just works | Needs `host.docker.internal` translation, which we already had to work around earlier — one more moving part |
| Reproducibility / easy rollback | Manual (reinstall, or Time Machine) | Easy (`docker pull`, restart) |
| Isolation from the rest of the Mac Studio | Less isolated | Better isolated |

**For a single-purpose local-LLM inference box, native is very likely the
better choice** — the GPU-acceleration question alone is significant
enough to check first. If Ollama turns out to be running in Docker right
now, **testing the exact same model natively before changing anything
else** is worth doing, since it might resolve a meaningful part of the
slowness on its own, for free, before any redesign work.

### Also check while you're there

- `ollama --version` — needs to be **v0.19.0 or later** (ideally the
  latest, v0.34.0) — this is the release that fixed the specific
  tool-call-leaking-as-text bug affecting the Qwen3.5/3.6 family, found
  and documented earlier tonight.
- How much RAM Activity Monitor shows actually free/available while a
  model is loaded and answering — tells us the real headroom for a
  larger `NUM_CTX` later.

## Step 2 — What the actual research says (not just the first suggestion that came up)

DuckDB was the first idea on the table, from a different AI's one-line
suggestion. Before committing to it, it got checked properly against the
alternatives — and it turns out DuckDB alone doesn't fix the thing that
actually broke tonight. Here's the real comparison.

### RAG (chunk + embed + retrieve) — ruled out as the primary approach

This is the most common answer to "document too big for context," so it's
worth ruling out explicitly rather than silently skipping it. Real
published research (a 2026 benchmark built specifically to test this)
found that RAG performs **badly at exactly the question types this
project exists for** — counting, min/max, top-k, "how many" — the
strongest tested approach scored only 1.51 out of a possible F1 of 100 on
these. The reason is structural, not a tuning problem: RAG retrieves the
*k* most relevant chunks, but a question like "how many Zubehör items are
there" needs *every single row*, not the most relevant few — missing even
one row silently gives a wrong count. **Not a fit for this project's core
need**, however tempting "just add a vector database" sounds.

### DuckDB (or any single `run_sql` tool) — real upside, but doesn't fix tonight's actual bug on its own

The real appeal: SQL is something every general-purpose LLM has been
extensively trained on, so replacing ~10 bespoke tools (`count_rows`,
`query_table`, `column_stats`, ...) with one `run_sql` tool should mean
far fewer "wrong tool, wrong argument name" mistakes, and cuts the tool
schema overhead that measurably tripled tonight.

**But here's what checking it properly found:** `run_sql` would still be
delivered to the model exactly the same way today's 13 tools are — through
Ollama's native `tools=` mechanism. That mechanism is exactly where the
actual bug lives (the Qwen3.5/3.6 tool-call-leaking-as-text bug from
`REDESIGN_PLAN.md`'s Step 1 findings). Fewer tools reduces *how often* the
model has to make a choice, and SQL is a more natural choice to make
correctly — genuine, real improvements — but it does not, by itself,
touch the mechanism that actually broke tonight. If the Ollama version is
the real cause (very plausible — see Step 1), a DuckDB rewrite alone
would still ride on the same fixed bug underneath a nicer interface.

### Structured output (`format`) — the piece that actually addresses the root mechanism

Real, separate Ollama feature, not something invented for this project:
since Ollama v0.5, you can pass a JSON Schema via the `format` parameter,
and Ollama constrains the model's output **at the token-generation level**
(masking any token that would violate the schema) so the result is
*always* valid, parseable JSON — a fundamentally different, more mature
mechanism than hoping a model's own training-time tool-calling convention
happens to match what Ollama's parser for that model family expects.

This is the one lever that's architecture-independent: whether the tool
surface ends up being today's ~10 functions, one `run_sql` tool, or
anything else, wrapping "what should happen next" in a `format`-
constrained JSON response (e.g. `{"action": "tool"|"answer", "tool":
"...", "arguments": {...}, "answer": "..."}`) and parsing that ourselves
sidesteps Ollama's model-specific native tool-call parsing entirely —
including whatever *future* tool-calling bug the next model family
happens to ship with, which native `tools=` calling has no defense
against.

Known limitation, so this isn't oversold: Ollama doesn't validate that
generation actually *finished* cleanly — if the model stops mid-response,
the grammar constraint doesn't retroactively fix an incomplete JSON
object. Basic validation-and-retry is still needed, but that's a far
smaller, more tractable failure mode than "wrote Python pseudocode
instead of any structured response at all."

### The actual recommendation: layered, not a single swap

1. **Ollama version + native/Docker check (Step 1)** — near-zero cost,
   directly targets the exact bug reported tonight. Do this first,
   always, regardless of anything below.
2. **Answer more questions without a tool call at all.** The `FAKTEN`
   block already precomputes some statistics — extending it (distinct
   counts, missing counts, duplicates, min/max, most-common-values, *per
   column*, computed once in code) so the model can read the answer
   directly for the most common question types shrinks the surface area
   where tool-calling reliability matters in the first place.
3. **For genuinely arbitrary questions that do need a tool**, use
   `format`-based structured output for the "which action, what
   arguments" decision instead of relying on native `tools=` — this is
   the actual fix for the failure mode observed tonight, independent of
   which specific tool surface sits behind it.
4. **Only after (1)-(3): decide whether the query tool itself should be
   SQL/DuckDB or a small number of well-designed Python functions.**
   Real trade-off, not urgent: SQL is more naturally trained into models
   and consolidates several tools into one, at the cost of a new `pip
   install duckdb` dependency (confirmed not in Open WebUI's bundled
   environment) and a real rewrite. A handful of consolidated Python
   tools needs no new dependency and less rewriting, at the cost of being
   a less naturally-trained skill for the model. This choice matters far
   less once (3) means either option gets its output parsed reliably.

## Step 3 — Build and test order (test before deploy, same discipline as before)

Each step below is independently testable and independently valuable —
this is deliberate, so progress doesn't depend on committing to the
riskiest change (a full DuckDB rewrite) before knowing whether the
cheaper fixes already solve most of the problem.

1. **Ollama version + native/Docker check.** Zero code changes. Do this
   first and report back — it might resolve the exact reported bug for
   free, which would change how urgent everything below actually is.
2. **Extend the precomputed `FAKTEN` facts block** so more common
   questions are answerable without any tool call. Testable immediately
   against the existing generated test documents and known answers.
3. **Switch tool dispatch to `format`-based structured output**, on
   today's existing tool set (no need to build DuckDB support first to
   test whether this alone fixes the reliability problem). Re-run
   `docs/question-coverage.md`'s question set and compare the score
   directly against tonight's 8/12 baseline — measured, not assumed.
4. **Only then, separately: evaluate SQL/DuckDB vs. consolidating the
   existing Python tools** as the thing structured output is dispatching
   to. Prototype locally against `tools/make_test_document.py`'s
   known-correct answers before touching the Mac Studio.
5. Deploy to the real Mac Studio and test with the actual office models
   only after a step measures better locally than what it's replacing.

## Open questions for you to decide before any of this starts

- Do you want the native-vs-Docker + Ollama-version check done and
  reported back before anything else starts, given it might change how
  much of the rest is even needed?
- Once shell access exists: is a one-time `pip install duckdb` (if step 4
  ends up favoring SQL) something you can do yourself, or does it need
  your Head of IT?
- Any other approach you want investigated before step 4's SQL-vs-Python
  decision gets made?
