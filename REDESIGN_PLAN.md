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

## Step 2 — The new architecture: DuckDB instead of one tool per operation

**Core idea:** instead of ~10 bespoke Python functions (`count_rows`,
`sum_column`, `query_table`, `column_stats`, `find_duplicates`, ...) each
with their own JSON schema, load each extracted table into
[DuckDB](https://duckdb.org/) (an embedded, file-free SQL database) and
give the model **one tool: `run_sql(query)`**. The model writes ordinary
SQL; DuckDB runs it against the real data; the result comes back.

**Why this is a real fix, not just a different tool:**

- Collapses ~10 tool schemas into 1 small one — directly addresses the
  measured context-overhead problem from Step 0 of this document.
- Every general-purpose LLM has been extensively trained on writing SQL —
  filtering, `GROUP BY`, `COUNT(DISTINCT ...)`, `WHERE ... LIKE`, sorting,
  joins — all the things we hand-built bespoke tools for tonight are
  *native* SQL operations. This should be far more reliable than the
  model correctly picking 1-of-13 custom tools and filling in our exact
  argument names.
- `search_text` (full-text search across the raw, un-tabular document
  text) and something like `document_info` stay as their own small tools
  — they're not naturally SQL-shaped and don't need to be forced into it.

**What doesn't change:** the German number-format cleanup
(`german.py`) still runs *before* a table is loaded into DuckDB, so
DuckDB only ever sees already-correctly-typed columns (real numbers as
numbers, EAN/barcode columns kept as text with leading zeros intact) —
none of tonight's correctness fixes are lost, only the query layer on top
changes.

**The one new dependency:** the `duckdb` Python package. Confirmed
tonight: **it is not currently in Open WebUI's bundled Python
environment** — this needs a one-time `pip install duckdb` into that
environment once there's real access to the Mac Studio. It's a single
lightweight package with no complex build step, so this should be a small
ask once shell access exists — but it is a new requirement, worth
flagging explicitly since "no pip installs required" was a hard
constraint earlier in this project when there was no shell access at all.

## Step 3 — Build and test order (test before deploy, same discipline as before)

1. Confirm Step 1's infrastructure check (native vs Docker, Ollama
   version) — do this first regardless of anything else, since it could
   change what "the problem" even is.
2. Prototype the DuckDB query layer **locally, on the dev PC**, against
   the same generated test documents already built
   (`tools/make_test_document.py`) and known-correct answers — before
   touching the Mac Studio at all.
3. Rebuild the Pipe Function around this: extraction stays the same,
   the ~10 bespoke table tools are replaced by `run_sql`, `search_text`
   and `document_info` stay.
4. Re-run the same realistic question set from
   `docs/question-coverage.md` against the new version, and compare the
   score directly against tonight's numbers (8/12 on the small local
   model) — an honest, measured comparison, not an assumption that SQL is
   automatically better.
5. Only after that comparison looks genuinely better: deploy to the real
   Mac Studio and test with the actual office models.

## Open questions for you to decide before any of this starts

- Does DuckDB need to go through your IT/Head of IT for the one-time
  `pip install`, or is that something you can do yourself once you have
  access?
- Do you want the native-vs-Docker check done and reported back *before*
  any DuckDB prototyping starts, or done in parallel?
- Any other approach you'd rather have investigated alongside DuckDB
  before committing to it?
