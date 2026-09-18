# Request: LAN access to Ollama on the Mac Studio (for development/testing)

**To:** Head of IT
**From:** Mirza (local-LLM project)
**What:** Make Ollama's API reachable from other machines on the office LAN,
not just from Open WebUI running on the Mac Studio itself.

## Why

I'm building and testing a document-preprocessing pipeline that needs to
call the Mac Studio's Ollama model directly from my own PC during
development — not through the Open WebUI chat window, through code. Right
now Ollama is only reachable as `localhost` on the Mac Studio, so Open WebUI
(which also runs there) can reach it, but nothing else on the network can.
I need that opened up for my dev/office machine specifically.

This is a development/testing need, not a request to expose anything to
the internet — LAN-only access is all that's needed.

## What this involves (technical summary)

Ollama listens on port `11434` and, by default, only accepts connections
from the same machine (`127.0.0.1`). Two changes are needed on the Mac
Studio:

1. **Tell Ollama to listen on the network, not just localhost.**
2. **Allow that port through the Mac's firewall** (if the macOS firewall is
   enabled).

## Steps (macOS / Mac Studio)

1. Open Terminal on the Mac Studio and run:
   ```bash
   launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
   ```
2. **Restart Ollama** so it picks up the new setting — quit it from the
   menu bar icon and reopen the Ollama app (a restart is required; it won't
   pick this up while already running).
3. If the macOS Application Firewall is on (**System Settings → Network →
   Firewall**), add an inbound allow rule for the Ollama app, or allow it
   when macOS prompts for incoming connections.
4. **Confirm the Mac Studio's LAN IP address** (**System Settings →
   Network**, or run `ipconfig getifaddr en0` in Terminal) and share it
   with me — that's the address my scripts will point at.
5. **Verify from the Mac Studio itself** that Ollama is listening correctly:
   ```bash
   curl http://localhost:11434/api/tags
   ```
   Should return a JSON list of installed models.
6. **Verify from my PC** (I'll do this once I have the IP): a matching
   `curl http://<mac-studio-ip>:11434/api/tags` from my machine should
   return the same list. If it times out, the firewall step above likely
   needs revisiting.

## Security notes (please read before doing this)

- Ollama's API has **no built-in authentication** — anyone who can reach
  `<mac-studio-ip>:11434` on the LAN can query the model and see what's
  loaded. This should stay LAN-only; it must not be port-forwarded through
  the office router to the public internet.
- If your firewall/router setup supports it, restricting this to a
  specific source IP (my office PC) rather than the whole LAN would be
  more conservative and is fine by me — I don't need broad access, just
  reachability from my one machine.
- This is intended as a **development-phase convenience**, not a permanent
  production configuration. Once the pipeline is packaged as an Open WebUI
  plugin (running on the Mac Studio itself, not connecting in from outside),
  this LAN exposure can likely be closed again — I'll flag that explicitly
  when we get there.

## Alternative, if opening this port isn't preferred

Open WebUI itself already has a network-reachable API (the same one the
web chat interface uses), protected by its own login/API-key system. It's
possible to route through that instead of hitting Ollama directly, but it
requires adapting the pipeline's tool-calling code to a different request
format and generating/managing an Open WebUI API key. I haven't built or
tested that path yet — happy to if the direct-Ollama-port approach isn't
workable on your end; let me know.

## What I'll do once this is open

Point my testing script at `http://<mac-studio-ip>:11434` (one command-line
flag, no source code changes needed) and validate the pipeline against real
company documents using the actual production model, instead of the small
substitute model I've been testing with on my own hardware.
