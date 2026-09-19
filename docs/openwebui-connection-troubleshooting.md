# Fixing Open WebUI pipeline problems: connection errors, hanging, and freezing

## Symptom: you edited the function's code, saved, but the OLD behavior persists

**Check this first if a fix "didn't work" — it's the single most likely cause,
confirmed during verification (2026-09-19).**

When you paste updated code into Admin Panel → Functions → (the function) →
code editor, the editor needs to fully select and replace the OLD content
before your paste lands. If the "select all" doesn't grab the *entire*
document (this can happen with large files in some editors), your paste
gets inserted alongside the old code instead of replacing it — both
versions end up in the same file. Since Python runs a file top-to-bottom,
whichever `class Pipe:` appears **later** in the file silently wins, even
if it's the old, buggy one. Nothing in the UI warns you this happened.

**How to check:** open the function's code editor, scroll to the very
bottom, and confirm there's only one `class Pipe:` in the whole file (use
Ctrl+F in the editor, or just read to the end). If you see two, the fix
never actually applied — delete everything in the editor manually (select
each line down to nothing, don't rely on a single "select all" for very
long files) and paste the correct version fresh.

**Faster and more reliable alternative:** delete the function entirely
(Admin Panel → Functions → the function → Function Menu → Delete) and
re-create it from scratch with **Create**, pasting the current version.
A fresh create can't have this leftover-old-code problem the way an
in-place edit can.

## Symptom: it hangs forever / browser shows "connection lost" / never finishes

**This was a real Open WebUI bug, not a document-parsing or Ollama problem.**
Fixed as of the pipeline's `v0.4.0`.

Root cause: the pipeline initially streamed its response as an `async`
generator (`async def pipe(...): yield ...`). Open WebUI version 0.6.43 (and
possibly nearby versions) has a confirmed bug where async-generator-based
pipes never send a completion signal back to the browser — the model
finishes generating, the server log shows it's done, but the chat UI never
finds out and hangs in "executing" indefinitely
([open-webui/open-webui#20196](https://github.com/open-webui/open-webui/issues/20196)).
If this happens repeatedly (e.g. several people/attempts leave connections
stuck open), it can also make Open WebUI itself become sluggish or
unreachable for everyone, not just the person testing the pipeline.

**The fix:** the pipeline now uses a **plain synchronous generator**
instead of an async one (the confirmed community workaround for this
bug) — same streaming behavior, without the broken completion signal.

**What to do:** re-paste the latest `openwebui/exact_count_pipe.py` from
this repo into Admin Panel → Functions (replace the old version), save,
and try again. If Open WebUI itself was left unreachable from a previous
hung attempt, it may need a restart first (ask IT, or restart it yourself
if you have that access) before testing again.

## What this error actually means

The exact message — *"failed to connect to Ollama. Please check that
Ollama is downloaded"* — is misleading. It's the `ollama` Python package's
own generic error text for "I couldn't reach the address I was given," and
it says that **even when Ollama is fully installed and running**. This was
confirmed directly: pointing the pipeline at a deliberately wrong address
on a machine where Ollama *is* running produces this exact message.

So: **this is a network/address problem, not a "reinstall Ollama"
problem.** Ollama is almost certainly fine on the Mac Studio — the pipe
just isn't reaching it from wherever it's actually running.

## The most likely cause: Docker

Open WebUI is very commonly run inside a Docker container. If that's the
case here, `localhost` (the pipe's old default) refers to *the container
itself*, not the Mac Studio — even though Open WebUI and Ollama both
"live" on the same physical machine, the container is functionally a
separate, isolated machine as far as networking is concerned.

**Check this first: is Open WebUI running in Docker on the Mac Studio?**
If you (or IT) have Terminal access there:
```bash
docker ps
```
If you see an `open-webui` (or similar) container listed, this is
almost certainly the issue.

## The fix — no code edits needed

The pipeline now has a configurable **OLLAMA_HOST** setting (a "Valve" in
Open WebUI's terminology) instead of a hardcoded address. To change it:

1. **Admin Panel → Functions**
2. Find **"Exact Count Document Assistant"**, click its **gear/settings icon**
3. Change **OLLAMA_HOST** depending on your setup:

| Setup | Value to try |
|---|---|
| Open WebUI in Docker, Ollama installed natively on the Mac (not in Docker) | `http://host.docker.internal:11434` |
| Open WebUI in Docker, Ollama ALSO in a Docker container (e.g. the official `docker-compose` bundle) | `http://ollama:11434` (or whatever the Ollama service is named in that compose file) |
| Neither is in Docker (both run natively on the Mac) | `http://localhost:11434` (the default — if this doesn't work in this scenario, Ollama itself likely isn't running: check with `ollama list` in Terminal) |

4. Save, then try uploading a file and asking a question again.

## If none of those work

Ask whoever set up Open WebUI on the Mac Studio (or check `docker ps` /
`docker inspect` yourself if you have access) exactly how it was deployed
— native install vs. Docker, and if Docker, what network mode and what the
Ollama container (if any) is named. That fully determines the right
`OLLAMA_HOST` value; the table above covers the common cases but isn't
exhaustive.

As a fallback sanity check, from a Terminal that has access to wherever
Open WebUI's process actually runs (inside the container, if it's
Dockerized: `docker exec -it <container-name> sh`), confirm the address is
reachable at all:
```bash
curl http://<candidate-ollama-host>:11434/api/tags
```
If that doesn't return a list of models, the address is still wrong (or
that path is still blocked) — keep trying the alternatives in the table.

## What was actually wrong with the pipeline code before this fix

The pipeline previously called Ollama with a hardcoded assumption that
`localhost:11434` was always reachable, with no way to override it short
of editing the source and re-pasting the whole function into Open WebUI.
It's now a Valve (configurable from the admin UI) and any connection
failure returns a clear, specific message in the chat itself — pointing at
this document — instead of silently failing or showing the generic
"check that Ollama is downloaded" text.
