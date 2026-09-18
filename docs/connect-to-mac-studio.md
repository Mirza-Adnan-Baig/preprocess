# Connecting this pipeline to the Mac Studio's model

Two parts: what IT needs to do once, and what you do every time you want
to test against the real model instead of your home PC's test model.

## Part 1 — send this to your Head of IT

[`docs/it-request-lan-ollama-access.md`](it-request-lan-ollama-access.md)
is a ready-to-forward request explaining exactly what needs opening (the
Mac Studio's Ollama port, LAN-only, not the internet) and the exact macOS
commands to do it. Forward that file as-is.

## Part 2 — once IT confirms it's open

**No code changes needed.** `scripts/ask.py` already supports pointing at
any Ollama server through an environment variable and a command-line flag
— you just have to set them each time you open a new terminal.

### Step 1: get two things from IT

- The Mac Studio's **LAN IP address** (e.g. `192.168.1.50`)
- The **exact model name/tag** as it's actually loaded on the Mac Studio —
  check this yourself in Open WebUI: open a new chat, look at the model
  picker dropdown at the top, that's the exact string to use (it might not
  be exactly `qwen3.6:27b` — confirm rather than assume).

### Step 2: point this terminal at the Mac Studio

Every time you open a new PowerShell window to work on this, run:

```powershell
$env:OLLAMA_HOST = "http://<mac-studio-lan-ip>:11434"
```

(Replace `<mac-studio-lan-ip>` with the real address from Step 1. This is
PowerShell syntax — `set OLLAMA_HOST=...`, the cmd.exe form, does **not**
work here and will silently fail to do anything.)

This only lasts for the current terminal window. If you open a new one
later, you'll need to run it again.

### Step 3: run your question, with the real model name

```powershell
python -m scripts.ask path\to\real_invoice.pdf "How many articles are on this invoice?" --model <exact-model-name-from-step-1>
```

If you don't pass `--model`, it defaults to `qwen2.5:7b` — the small model
used for testing on the home PC, **not** what's on the Mac Studio. Always
pass `--model` explicitly when talking to the Mac Studio.

### Step 4: confirm it's actually talking to the Mac Studio, not failing silently

If `$env:OLLAMA_HOST` is wrong, unreachable, or you forgot to set it, the
script won't crash oddly — it'll just try `localhost` (nothing there on
your office PC) and the request will time out or error. If that happens:

```powershell
curl http://<mac-studio-lan-ip>:11434/api/tags
```

If that doesn't return a list of models, the network path isn't open yet
— that's an IT question (Part 1), not a code problem.
