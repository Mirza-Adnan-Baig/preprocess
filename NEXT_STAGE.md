# Next stage — once you have Mac Studio access

This is the plan for after admin/shell access to the Mac Studio, discussed
2026-09-23. Nothing here has been implemented yet.

## Priority order

1. Test on the real hardware with the real models (27B/35B) against real
   documents — the biggest untested variable so far.
2. Fix end-user permissions (below) — right now only an admin can use this
   at all.
3. Consider OCR now that shell access makes installing Tesseract possible.
4. Revisit MCP/a separate server only if a concrete need for it shows up
   (see "Plugin or MCP server?" below).

## The permission system for models — why "it only works for me" is expected

Open WebUI restricts who can use a Function/Model by default. This is not
something in our code — it's Open WebUI's own access-control system, and it
applies to every model, not just ours.

**Confirmed directly** (created a normal, non-admin test account on a local
Open WebUI instance and checked): a plain "user"-role account sees **zero
models** in the chat model picker — not just the FARO assistant, nothing at
all, including the base Ollama model. Everything is admin-only until an
admin explicitly grants access.

**What the fix is not:** Admin Panel → Users → Groups → "Default
Permissions" → "Models Access" looks like the obvious lever, but it
controls whether a normal user can *create their own* models/workspace
items — not whether they can *use* a model an admin already set up. Turning
that on does not fix this.

**What the fix actually is:** Open WebUI has a per-model sharing/access-grant
system — each model (including the one this Function creates) can be
explicitly shared with specific users, a group, or everyone. This is the
real mechanism (confirmed by reading Open WebUI's own permission-checking
code), but the exact click-path in the Admin Panel UI needs to be walked
through together on the real instance — it wasn't fully pinned down on the
local test copy. When you have access, the places to look:

- The model's own settings (Workspace → Models, or wherever the FARO
  assistant's entry is editable) — look for a "Share" / "Visibility" /
  "Access" option.
- Admin Panel → Users → Groups — if a group-based approach makes more
  sense for FARO (e.g. a "Warehouse" or "All Staff" group), create the
  group first, then grant the model to that group rather than to
  individual users one at a time.

**Recommended approach for a real multi-user rollout:** create one group
(e.g. "FARO Staff") containing everyone who should have access, and grant
that group read access to the model — easier to manage than granting
individual users, and new hires just get added to the group.

## Plugin or MCP server?

**Not recommended as the next step.** The current design — an Open WebUI
Pipe Function — works, and every fix made so far (fitting the document
into the model's context window, catching a model that answers without
using a tool, keeping the connection alive during long answers) is logic
this project controls directly inside that function.

An MCP server would hand tool-calling over to Open WebUI's own generic MCP
handling instead of this project's own logic. That's a real rewrite, none
of it tested, and it would risk reopening problems already solved. It
becomes worth it only if there's a real need to use these same document
tools from somewhere other than Open WebUI's chat (e.g. Slack, email, a
different app) — not needed today.

## What OCR would need (if pursued)

Currently out of scope — scanned/photographed documents get rejected or
flagged as unreadable. With real shell access this becomes possible:

- Install the Tesseract OCR binary, **plus the German (`deu`) language
  pack**, not just English — every real document is German.
- A vision-capable model pulled into Ollama would be a stronger
  alternative to Tesseract for reading a photo like the one shared during
  testing, worth comparing before committing to either path.

Not started — flagging so it's on the list for when scanned documents come
up as a real, recurring need rather than a hypothetical one.
