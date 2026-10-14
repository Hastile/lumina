"""Running console tools without opening a console.

The desktop build is `--windowed`, so the app has no console of its own. On
Windows a child process that wants one therefore gets a *brand new* console
window: it appears on top of whatever the user is doing, lives as long as the
call, and vanishes. The ingest pipeline calls ffprobe and ffmpeg once per
file - and several times per video - so a batch turns into a stream of black
rectangles flashing over the screen.

CREATE_NO_WINDOW suppresses that. It changes nothing else: the child still
runs, still writes to the pipes it was given, and still returns its exit
code. Output is already captured at every call site, so there was never a
console anyone was meant to read.

Exported as a kwargs mapping rather than a wrapper function so call sites
keep their own timeouts, encodings and error handling, and the change at each
one is a single `**NO_WINDOW`.
"""

from __future__ import annotations

import subprocess
import sys

# Empty off Windows: the flag does not exist there, and no other platform
# creates a window for a console child in the first place.
NO_WINDOW: dict[str, int] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)
