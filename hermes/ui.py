"""Tiny ANSI palette for the REPL.

Colors switch off automatically when stdout is not a tty, NO_COLOR is set,
or TERM=dumb, so piped output and dumb terminals stay clean. Anything that
ends up in a prompt for the model (e.g. the gpu_status env line) must stay
uncolored — only paint what goes straight to the operator's screen.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager


def _detect() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


ENABLED = _detect()


def _paint(code: str):
    def fn(text: object) -> str:
        if not ENABLED:
            return str(text)
        return f"\x1b[{code}m{text}\x1b[0m"
    return fn


bold = _paint("1")
dim = _paint("2")
red = _paint("31")
green = _paint("32")
yellow = _paint("33")
magenta = _paint("35")
cyan = _paint("36")


@contextmanager
def heartbeat(label: str, interval: float = 15.0, printer=print):
    """Proof-of-life for a blocking call with no output of its own (a model
    reply the size of the whole context, a remote command reading through
    large files, ...). Without this, a legitimately slow operation is
    indistinguishable from a dead one — the operator sees zero characters
    either way and has to guess whether to keep waiting or Ctrl-C. Prints
    `label (Ns)` on a timer until the `with` block exits; silent (and
    zero-cost past thread setup) for anything that finishes inside the first
    interval, which is the common case."""
    stop = threading.Event()
    started = time.monotonic()

    def _beat():
        while not stop.wait(interval):
            elapsed = int(time.monotonic() - started)
            printer(dim(f"  … {label} ({elapsed}s)"))

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=1)
