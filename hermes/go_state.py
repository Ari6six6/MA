"""Cross-process bookkeeping for `go`: one small JSON file per space under
~/.hermes/go/, so a background worker (or a foreground `run`) started by one
`hermes` invocation is visible to another — `go status`/`attach`/`say` run in
a fresh process entirely. One file per space (not a single shared file) so two
spaces running at once never race on the same read-modify-write.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from hermes.config import hermes_home
from hermes.ssh import pid_alive

DEFAULT_SPACE = "space"
GO_MAX_RUN_SECONDS = 42 * 60  # arbitrary, hard, never extended by an interjection


def go_dir() -> Path:
    d = hermes_home() / "go"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(space: str) -> Path:
    return go_dir() / f"{space}.log"


def inbox_path(space: str) -> Path:
    return go_dir() / f"{space}.inbox.jsonl"


def prompt_tmp_path(space: str) -> Path:
    return go_dir() / f"{space}.prompt.tmp"


def state_path(space: str) -> Path:
    return go_dir() / f"{space}.state.json"


def start_entry(space: str, pid: int, kind: str, log: str | None = None,
                 inbox: str | None = None) -> None:
    """Register a live process for `space`. `kind` is "go" (detached worker,
    has a log/inbox to attach/say to) or "run" (foreground REPL call —
    registered purely so other processes see the space is busy)."""
    entry = {
        "pid": pid,
        "kind": kind,
        "run_id": None,
        "started_epoch": time.time(),
        "started_ts": time.strftime("%Y-%m-%d %H:%M"),
        "log_path": log,
        "inbox_path": inbox,
    }
    path = state_path(space)
    path.write_text(json.dumps(entry, indent=2) + "\n")
    os.chmod(path, 0o600)


def update_run_id(space: str, run_id: int) -> None:
    path = state_path(space)
    try:
        entry = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    entry["run_id"] = run_id
    path.write_text(json.dumps(entry, indent=2) + "\n")


def clear_entry(space: str) -> None:
    state_path(space).unlink(missing_ok=True)


def active_entry(space: str) -> dict | None:
    """The live entry for `space`, or None if nothing's running there. A dead
    pid self-heals: the stale file is deleted right here, so callers never
    have to think about crash cleanup separately."""
    path = state_path(space)
    try:
        entry = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not pid_alive(entry.get("pid", 0)):
        path.unlink(missing_ok=True)
        return None
    return entry


def drain_inbox(inbox_path) -> list[str]:
    """Atomically pop every pending operator message a separate `go`/`go say`
    process wrote. Renaming the file aside before reading (instead of
    read-then-truncate) means a writer racing this drain either lands in the
    detached old file — read right here — or recreates the path fresh, picked
    up on the next drain: never silently lost, only possibly delayed. Shared by
    the run loop's per-turn poll and the `ask_operator` tool's blocking wait, so
    a reply lands in exactly one of them, never both."""
    inbox_path = Path(inbox_path)
    if not inbox_path.exists():
        return []
    tmp = inbox_path.with_name(inbox_path.name + f".draining.{os.getpid()}")
    try:
        inbox_path.rename(tmp)
    except OSError:
        return []
    try:
        text = tmp.read_text()
    finally:
        tmp.unlink(missing_ok=True)
    out: list[str] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("text")
        if isinstance(msg, str) and msg.strip():
            out.append(msg.strip())
    return out


def list_active() -> dict[str, dict]:
    """Every space with a live process, pruning dead ones as a side effect."""
    out: dict[str, dict] = {}
    for path in go_dir().glob("*.state.json"):
        space = path.name[: -len(".state.json")]
        entry = active_entry(space)
        if entry is not None:
            out[space] = entry
    return out
