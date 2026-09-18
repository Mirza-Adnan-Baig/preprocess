"""
title: Diagnostic: Streaming Timing Test
author: Mirza
version: 1.0.0

Does NOTHING with your document, Ollama, or extraction — just yields 6
short messages, 2 seconds apart, over 10 seconds total. No dependencies
beyond the standard library, nothing that can be slow or fail.

Purpose: isolate whether Open WebUI in this specific setup shows streamed
text progressively at all, or whether it always waits for the whole
response before showing anything (which would mean the "freezing" is not
about async/sync generators — it would mean the real work underneath is
just slow, and the UI just doesn't display progress for it either way).

Install: Admin Panel -> Functions -> Create -> paste this whole file ->
Save -> toggle Active -> select "Diagnostic: Streaming Timing Test" as the
model -> send any message (no file needed) -> WATCH THE SCREEN closely and
time it with a clock/phone.

Report back exactly what you see:
- Do the 6 messages appear one at a time, roughly 2 seconds apart, over
  about 10-12 seconds total? -> streaming works here, the real pipeline's
  slowness is genuine backend time, not a display bug.
- Does NOTHING appear for a while, then all 6 lines show up at once? ->
  streaming isn't being displayed incrementally in this Open WebUI setup
  at all, regardless of sync/async -- different problem, different fix.
"""

import time


class Pipe:
    def __init__(self):
        self.id = "diagnostic_streaming_timing_test"
        self.name = "Diagnostic: Streaming Timing Test"

    def pipe(self, body: dict, __files__: list = None, __user__: dict = None):
        for i in range(1, 7):
            yield f"Chunk {i}/6 sent at t={2*(i-1)}s\n\n"
            time.sleep(2)
        yield "Done. If you saw these appear one at a time over ~10-12 seconds, streaming works here."
