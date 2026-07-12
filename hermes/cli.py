"""The Hermes REPL — short commands for a phone keyboard.

  session [text]    the default way to work: a live session you sit INSIDE
                     with the agent, sharing one time budget (up to
                     GO_MAX_RUN_SECONDS). You send a message, it works
                     narrated in front of you, it can pause to ask YOU
                     something, then it hands the turn back — `done` ends it.
                     Not a job you fire off; a room you're both in. (alias: s)
  go <text>         fire-and-forget instead: a detached background process
                     (survives closing the phone), hard-capped at
                     GO_MAX_RUN_SECONDS. Use it when you DON'T want to sit
                     with it; talk to it with `go say`, watch with `go attach`.
  go attach [space] watch a running `go` live — narration + inner voice —
                     detach any time with Ctrl-C, it keeps running
  go say [space] <text>   send a background `go` a message while it's running
                     (also how you answer when it asks YOU something —
                     it can pause mid-run and wait for your reply)
  go stop [space|all]  killswitch: stop a detached run dead (alias: stop)
  go status         list what's running
  run <text>        a single foreground exchange (one prompt, one run), inside
                     the current space (alias: r)
  space             new/use/list — a space is one workbench of work: its own
                     mission, files, and run history (alias: p)
  gpu ...           attach/serve/status/tunnel/up/down (alias: g)
  mission/notes/history/summaries/tools/config/persona/help/quit
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from hermes import __version__, agent, go_state
from hermes import hosts as hosts_mod
from hermes.config import Config, hermes_home, persona_path
from hermes.go_state import DEFAULT_SPACE, GO_MAX_RUN_SECONDS
from hermes.gpu import (
    endpoint_from_state,
    load_gpu_state,
    probe_net_isolation,
    save_gpu_state,
)
from hermes.llm import make_backend
from hermes.project import Project, ProjectError
from hermes.sandbox import capabilities as sandbox_capabilities, local_endpoint
from hermes.ssh import SSHEndpoint, SSHError, kill_pid, parse_ssh_string, pid_alive
from hermes.ui import bold, cyan, dim, green, magenta, red, yellow

BANNER = f"{bold(magenta('hermes'))} {dim('v' + __version__)}"


# ---------------------------------------------------------------- helpers
def _projects_dir(cfg) -> Path:
    return Path(cfg.get("projects_dir")).expanduser()


def _current_project(cfg) -> Project | None:
    name = cfg.get("current_project")
    if not name:
        return None
    try:
        return Project.load(_projects_dir(cfg), name)
    except ProjectError:
        return None


def _ensure_space(cfg) -> Project:
    """`go`'s no-ceremony entry point: use the current space if one is selected,
    else silently create-and-select the default one. `space new` still makes a
    separate workbench if you want more than one — this just means `go` never
    blocks on "no space selected"."""
    project = _current_project(cfg)
    if project is not None:
        return project
    name = cfg.get("current_project") or DEFAULT_SPACE
    pdir = _projects_dir(cfg)
    try:
        project = Project.load(pdir, name)
    except ProjectError:
        project = Project.create(pdir, name)
    cfg.set("current_project", name, coerce=False)
    cfg.save()
    return project


def _probe_vllm(cfg) -> bool:
    try:
        url = f"http://127.0.0.1:{cfg.get('local_port', 8000)}/v1/models"
        return httpx.get(url, timeout=4).status_code == 200
    except httpx.HTTPError:
        return False


def _remote_server_alive(ep) -> bool:
    """Is a vLLM/llama.cpp process still running on the box, per ~/vllm.pid?
    Distinguishes "still warming up" from "never launched" when the tunnel
    is up but the endpoint isn't answering yet."""
    if ep is None:
        return False
    rc, out, _ = ep.run(
        "cat ~/vllm.pid 2>/dev/null && kill -0 $(cat ~/vllm.pid) 2>/dev/null && echo RUNNING",
        timeout=15,
    )
    return "RUNNING" in out


def _vllm_down_hint(ep) -> str:
    """Why the endpoint isn't reachable, for status/tunnel/run messages."""
    if ep is None:
        return "`gpu attach` + `gpu serve` first"
    if _remote_server_alive(ep):
        return ("server process is up but not answering yet — still loading "
                "weights (`remote tail -n 50 ~/vllm.log`)")
    return "no model server running on the box — `gpu serve` first"


def _ensure_tunnel(cfg, state) -> None:
    """Best effort: restart the tunnel if the pid died."""
    ep = endpoint_from_state(state)
    if ep is None:
        return
    if pid_alive(state.get("tunnel_pid", 0)) and _probe_vllm(cfg):
        return
    if state.get("tunnel_pid"):
        kill_pid(state["tunnel_pid"])
    pid = ep.start_tunnel(cfg.get("local_port", 8000), cfg.get("gpu_port", 8000))
    state["tunnel_pid"] = pid
    save_gpu_state(state)


def _gpu_status_line(cfg, state) -> str:
    if not state.get("host"):
        return "not attached"
    up = "vllm:up" if _probe_vllm(cfg) else "vllm:DOWN"
    ctx = state.get("served_ctx")
    return f"{state['host']}:{state['port']} ({up}{f', ctx {ctx}' if ctx else ''})"


def _sandbox_status_line() -> str:
    caps = sandbox_capabilities(local_endpoint())
    if not caps["runtime"]:
        return "local — no container runtime yet (installs on first `build serve`)"
    bits = [caps["runtime"]] + (["kvm"] if caps["kvm"] else [])
    return "local (" + ", ".join(bits) + ")"


def _edit_file(path: Path) -> None:
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(path)])


def _pick_model(cfg):
    """Let the operator choose which model to serve, defaulting to the one the
    config already points at. Persists the choice so `run` serves the same
    identity. Returns the chosen ModelSpec, or None if cancelled."""
    from hermes.models import model_list, resolve

    specs = model_list()
    current = resolve(cfg)
    default_idx = next((i for i, s in enumerate(specs) if s.key == current.key), 0)
    print(dim("which model?"))
    for i, s in enumerate(specs):
        tag = green("ready") if s.ready else yellow("experimental")
        here = cyan(" ← current") if s.key == current.key else ""
        print(f"  {cyan(f'[{i + 1}]')} {s.label} [{tag}]{here}")
    try:
        raw = input(f"model [{default_idx + 1}]? ").strip()
    except EOFError:
        raw = ""
    if not raw:
        spec = specs[default_idx]
    else:
        try:
            spec = specs[int(raw) - 1]
            if int(raw) < 1:
                raise IndexError
        except (ValueError, IndexError):
            print(yellow("not a listed choice — cancelled"))
            return None
    # The served name is what the OpenAI client (llm.py) sends; keep it in sync.
    cfg.set("model_id", spec.key)
    cfg.set("model", spec.served_name)
    cfg.set("quantization", spec.quantization)
    # Apply this model's tuned build — sampling, completion budget, stall
    # tolerance — so the agent loop and client serve its optimized profile, not
    # the previous model's. (The Hermes profile equals the app defaults.)
    for key, value in spec.runtime_config().items():
        cfg.set(key, value)
    cfg.save()
    return spec


# ---------------------------------------------------------------- commands
def _prepare_run(cfg):
    """Common setup for `run`/`go`: makes sure the GPU tunnel + vLLM endpoint
    are reachable and builds the env dict the package needs. Returns
    (gpu, sandbox, env, backend), or None (having already printed why) if the
    backend isn't reachable."""
    state = load_gpu_state()
    gpu = endpoint_from_state(state)
    sandbox = local_endpoint()  # the air-gapped exec container runs on this same box
    if cfg.get("backend") != "mock":
        if state.get("host"):
            _ensure_tunnel(cfg, state)
        if not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(f" — {_vllm_down_hint(gpu)} "
                  "(or `config set backend mock` for a dry run)."))
            return None
    from hermes.models import resolve
    spec = resolve(cfg)
    env = {
        "gpu_status": _gpu_status_line(cfg, state),
        "sandbox_status": _sandbox_status_line(),
        "remote_workspace": state.get("remote_workspace", "~/hermes-workspace"),
        "context_window": state.get("served_ctx", 0),
        "model_identity": spec.identity,
        "model_tool_guidance": spec.tool_guidance,
    }
    return gpu, sandbox, env, make_backend(cfg)


def cmd_run(cfg, args: str) -> None:
    if not args.strip():
        print(dim("usage: run <prompt>"))
        return
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet")
              + dim(" — just use `go <something>`, which starts one for you"))
        return
    busy = go_state.active_entry(project.name)
    if busy:
        print(yellow(f"'{project.name}' is busy") + dim(
            f" — a `{busy.get('kind', 'go')}` is already working there (pid {busy['pid']})."))
        return
    prepared = _prepare_run(cfg)
    if prepared is None:
        return
    gpu, sandbox, env, backend = prepared
    go_state.start_entry(project.name, os.getpid(), kind="run")
    try:
        agent.run(project, args.strip(), cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
                  on_run_started=lambda run_id, _run_dir: go_state.update_run_id(project.name, run_id),
                  background_housekeeping=True)
    finally:
        go_state.clear_entry(project.name)


def cmd_session(cfg, args: str) -> None:
    """A live session you sit INSIDE with the agent — not a job you fire off.

    One shared time budget (the same 42-minute ceiling as `go`, a max and not a
    target: `session hey` is over in seconds). You drive: type your first
    request, watch it work narrated in the foreground, answer when it asks you
    something, and send the next message when it hands the turn back. `done` /
    `exit` (or Ctrl-C at the prompt) ends the session; a single Ctrl-C while
    it's working stops just that piece and hands you back the turn. Each
    exchange is a fresh run that inherits the previous one's summary, so the
    thread of what you're building carries across the whole session, and the
    normal y/n gates apply because you're right here."""
    project = _ensure_space(cfg)
    busy = go_state.active_entry(project.name)
    if busy:
        print(yellow(f"'{project.name}' is busy") + dim(
            f" — a `{busy.get('kind', 'go')}` is already working there (pid {busy['pid']})."))
        return
    prepared = _prepare_run(cfg)
    if prepared is None:
        return
    gpu, sandbox, env, backend = prepared

    total = GO_MAX_RUN_SECONDS
    started = time.monotonic()
    mins = total // 60

    def ask_stdin(_question: str) -> str:
        # The question banner is already printed by the tool; just take the line.
        try:
            return input(magenta("  reply> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    go_state.start_entry(project.name, os.getpid(), kind="session")
    print(dim(f"— session in '{project.name}' — we're in this together, up to "
              f"{mins} min. Send a message; it works, then hands you back the "
              f"turn. `done` ends it. —"))
    first = args.strip()
    exchanges = 0
    try:
        while True:
            remaining = total - (time.monotonic() - started)
            if remaining <= 5:
                print(dim(f"— the {mins}-minute session budget is spent — ending the session —"))
                break
            if first:
                msg, first = first, ""
            else:
                try:
                    msg = input(magenta("you> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
            if msg.lower() in ("done", "exit", "quit", "bye"):
                break
            if not msg:
                continue
            exchanges += 1
            agent.run(
                project, msg, cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
                max_run_seconds=int(remaining),
                ask_operator_fn=ask_stdin,
                on_run_started=lambda rid, _d: go_state.update_run_id(project.name, rid),
                background_housekeeping=True,
            )
            left = int(max(0, total - (time.monotonic() - started)) // 60)
            print(dim(f"— your turn — ~{left} min left in the session (`done` to end) —"))
    finally:
        go_state.clear_entry(project.name)
    # Surface the last turn's background librarian work before handing back the prompt.
    agent.flush_housekeeping()
    print(dim(f"— session ended — {exchanges} exchange(s) —"))


# The debate contract: injected as this run's system framing (not persona.md,
# which stays yours to edit). It tells the agent this is a table, not a task —
# so paired with stall/phantom nudges at 0, a pure-prose turn is a valid answer.
DEBATE_FRAMING = """\
You are at the table with the operator — a live, unhurried debate, not a job to
finish. The person speaking is the operator described in your persona: your
principal and your partner, working with you, not against you. Reason out loud
in plain language. Say plainly what you are doing and why, and withhold nothing
you know that bears on what they're asking. You are NOT required to call a tool
or produce a deliverable this turn — thinking it through together IS the work.
Use tools when they genuinely help (read a file they point you to, check a
fact), then come back to the conversation. Speaking plainly here doesn't retire
the narrator voice — an occasional <narrate>...</narrate> aside is still yours
to use if the moment calls for it. When you've said your piece, stop and hand
the turn back so they can answer."""


def cmd_debate(cfg, args: str) -> None:
    """A table, not a task: sit across from the agent and talk it out.

    Same 42-minute sitting and the same live back-and-forth as `session`, but
    the "act or finish_run" pressure is off (stall/phantom nudges = 0), so a
    turn that's pure reasoning is a valid answer instead of something the
    harness bounces. The agent knows it's talking to the operator (persona) and
    is told to withhold nothing. `persona`/`persona edit` reshapes who it is
    without leaving the table; `done`/`exit` (or Ctrl-C at the prompt) ends it."""
    project = _ensure_space(cfg)
    busy = go_state.active_entry(project.name)
    if busy:
        print(yellow(f"'{project.name}' is busy") + dim(
            f" — a `{busy.get('kind', 'go')}` is already working there (pid {busy['pid']})."))
        return
    prepared = _prepare_run(cfg)
    if prepared is None:
        return
    gpu, sandbox, env, backend = prepared

    total = GO_MAX_RUN_SECONDS
    started = time.monotonic()
    mins = total // 60

    def ask_stdin(_question: str) -> str:
        try:
            return input(magenta("  reply> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    go_state.start_entry(project.name, os.getpid(), kind="debate")
    print(dim(f"— at the table in '{project.name}' — up to {mins} min, no rush. "
              f"Talk it through; `persona` to reshape who you're talking to; "
              f"`done` to get up. —"))
    first = args.strip()
    exchanges = 0
    try:
        while True:
            remaining = total - (time.monotonic() - started)
            if remaining <= 5:
                print(dim(f"— the {mins} minutes are up — leaving the table —"))
                break
            if first:
                msg, first = first, ""
            else:
                try:
                    msg = input(magenta("you> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
            low = msg.lower()
            if low in ("done", "exit", "quit", "bye"):
                break
            if low in ("persona", "persona edit"):
                _edit_file(persona_path())  # reshape who's at the table, mid-sitting
                continue
            if not msg:
                continue
            exchanges += 1
            agent.run(
                project, msg, cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
                max_run_seconds=int(remaining),
                ask_operator_fn=ask_stdin,
                stall_nudges=0, phantom_nudges=0, extra_system=DEBATE_FRAMING,
                on_run_started=lambda rid, _d: go_state.update_run_id(project.name, rid),
                background_housekeeping=True, mode="debate",
            )
            left = int(max(0, total - (time.monotonic() - started)) // 60)
            print(dim(f"— your turn — ~{left} min left at the table (`done` to end) —"))
    finally:
        go_state.clear_entry(project.name)
    # Surface the last turn's background librarian work before leaving the table.
    agent.flush_housekeeping()
    print(dim(f"— left the table — {exchanges} exchange(s) —"))


# The self-improvement contract: like the debate contract, but pointed at the
# machine itself. It tells the agent this sitting is about its OWN code, that the
# whole record of what it has done is in view, and that edits are real but gated.
IMPROVE_FRAMING = """\
You are at the table with the operator, and this sitting is about YOU — the
Hermes machinery you run on — not an outside task. Everything you have done is in
view: your mission, your notes, your run summaries, and the WORKSPACE CATALOG of
what you've produced. You can read your own source with list_hermes_source /
read_hermes_source, and propose changes with write_hermes_source /
edit_hermes_source. Every edit pauses for the operator's yes/no with the diff AND
the test-suite result shown — a change that breaks the tests is visible before
it's kept, and a decline reverts it cleanly. Some files (the safety gates
themselves) refuse edits outright; that is by design, not a bug to route around.
Reason out loud about what actually RECURS in the record — the friction worth
removing — and prefer small, tested, reversible changes over sweeping ones. The
narrator voice (<narrate>...</narrate>) is still yours here too, sparingly. When
you've said your piece, hand the turn back."""


def cmd_improve(cfg, args: str) -> None:
    """Sit at the table to work on Hermes ITSELF: same unhurried sitting as
    `debate`, but the agent can read and edit its own source, every edit gated by
    your y/n with the test suite run against it first (the scoreboard). Its whole
    record — notes, run summaries, the workspace catalog — is in view, so "what
    do you think needs improvement, looking at what you've done?" is a real
    question it can answer from the evidence.

    Self-build is turned on for THIS sitting only (not saved), so the gate closes
    again when you get up. `done`/`exit`/Ctrl-C ends it."""
    project = _ensure_space(cfg)
    busy = go_state.active_entry(project.name)
    if busy:
        print(yellow(f"'{project.name}' is busy") + dim(
            f" — a `{busy.get('kind', 'go')}` is already working there (pid {busy['pid']})."))
        return
    prepared = _prepare_run(cfg)
    if prepared is None:
        return
    gpu, sandbox, env, backend = prepared

    total = GO_MAX_RUN_SECONDS
    started = time.monotonic()
    mins = total // 60

    def ask_stdin(_question: str) -> str:
        try:
            return input(magenta("  reply> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    # Open the self-build gate for this sitting only — remember the prior value
    # and restore it on the way out, and never save(), so the gate is not left
    # open on disk after you get up.
    prior_self_build = cfg.get("self_build_enabled", False)
    cfg.set("self_build_enabled", True, coerce=False)
    tests_on = cfg.get("self_build_run_tests", True)

    go_state.start_entry(project.name, os.getpid(), kind="improve")
    print(dim(f"— working on Hermes itself in '{project.name}' — up to {mins} min. "
              f"It can read + edit its own source; every edit needs your y/n "
              f"{'with the test result shown' if tests_on else '(tests OFF)'}. "
              f"`done` to get up. —"))
    if not prior_self_build:
        print(dim("  (self-build is on for this sitting only — it closes when "
                  "you leave)"))
    first = args.strip()
    exchanges = 0
    try:
        while True:
            remaining = total - (time.monotonic() - started)
            if remaining <= 5:
                print(dim(f"— the {mins} minutes are up — leaving the table —"))
                break
            if first:
                msg, first = first, ""
            else:
                try:
                    msg = input(magenta("you> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
            low = msg.lower()
            if low in ("done", "exit", "quit", "bye"):
                break
            if low in ("persona", "persona edit"):
                _edit_file(persona_path())
                continue
            if not msg:
                continue
            exchanges += 1
            agent.run(
                project, msg, cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
                max_run_seconds=int(remaining),
                ask_operator_fn=ask_stdin,
                stall_nudges=0, phantom_nudges=0, extra_system=IMPROVE_FRAMING,
                on_run_started=lambda rid, _d: go_state.update_run_id(project.name, rid),
                background_housekeeping=True,
            )
            left = int(max(0, total - (time.monotonic() - started)) // 60)
            print(dim(f"— your turn — ~{left} min left (`done` to end) —"))
    finally:
        cfg.set("self_build_enabled", prior_self_build, coerce=False)  # close the gate
        go_state.clear_entry(project.name)
    # Surface the last turn's background librarian work before leaving the table.
    agent.flush_housekeeping()
    print(dim(f"— left the table — {exchanges} exchange(s) — self-build gate closed —"))


def cmd_go(cfg, args: str) -> None:
    """The one verb for a space: say something. Nothing running yet? It
    starts, and you watch it live. Something already running? What you typed
    gets woven straight into that conversation, and you watch it land — no
    separate `say` step needed for the common case. Either way you're looking
    at it happen in real time; Ctrl-C is the only way to step back, and it
    keeps running when you do (survives closing the terminal entirely).

    The conversation runs both ways: the agent can pause mid-run and ask YOU a
    question when it hits a genuinely influential fork (via its `ask_operator`
    tool). It shows up right here in the live view, and you answer the same way
    you say anything else — Ctrl-C out of the tail if you're watching, then
    `go <your answer>` (or `go say <space> <answer>`). It picks the reply up and
    carries on."""
    parts = args.split(maxsplit=1)
    sub, rest = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
    if sub == "attach":
        cmd_go_attach(cfg, rest)
        return
    if sub == "say":
        cmd_go_say(cfg, rest)
        return
    if sub == "status":
        cmd_go_status(cfg, rest)
        return
    if sub in ("stop", "kill"):
        cmd_go_stop(cfg, rest)
        return

    text = args.strip()
    project = _ensure_space(cfg)
    busy = go_state.active_entry(project.name)

    if busy:
        if not busy.get("inbox_path"):
            print(yellow(f"'{project.name}' is running in the foreground elsewhere") + dim(
                " — nothing to watch or send to here."))
            return
        if text:
            _go_append_inbox(project.name, busy, text)
        else:
            print(dim(f"— already working in '{project.name}' — watching it live —"))
        _go_tail(project.name, busy)
        return

    if not text:
        print(dim("usage: go <prompt>  |  go attach [space]  |  go say [space] <text>"
                   "  |  go stop [space|all]  |  go status"))
        return
    prepared = _prepare_run(cfg)  # fast fail here; the worker rebuilds its own gpu/sandbox/env/backend
    if prepared is None:
        return

    prompt_file = go_state.prompt_tmp_path(project.name)
    prompt_file.write_text(text)
    log_p = go_state.log_path(project.name)
    inbox_p = go_state.inbox_path(project.name)
    inbox_p.unlink(missing_ok=True)  # discard stale unread messages from a previous run in this space

    with open(log_p, "w") as log_f:
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "hermes.go_worker", project.name, str(prompt_file)],
                stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError as e:
            print(red(f"could not start background run: {e}"))
            prompt_file.unlink(missing_ok=True)
            return

    go_state.start_entry(project.name, proc.pid, kind="go", log=str(log_p), inbox=str(inbox_p))
    print(dim(f"— '{project.name}' started (up to {GO_MAX_RUN_SECONDS // 60} min) — "
               "watching live · type `go <message>` to steer it · Ctrl-C leaves it running —"))
    _go_tail(project.name, go_state.active_entry(project.name), replay="all")


def _go_target_space(cfg, name: str) -> str:
    return name.strip() or cfg.get("current_project") or DEFAULT_SPACE


def _go_append_inbox(space: str, entry: dict, text: str) -> None:
    line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"), "text": text.strip()})
    with open(entry["inbox_path"], "a") as f:
        f.write(line + "\n")
    # Anchor the message you just sent, clearly attributed, so it doesn't vanish
    # into the stream — and set the expectation that it's queued, not instant.
    print()
    print(bold(green("  you: ")) + text.strip())
    print(dim("  delivered — it reads this at its next step; watching for the reply…"))


def _go_tail(space: str, entry: dict, replay: str = "tail") -> None:
    """Stream a running space's live output until it finishes or you Ctrl-C out
    — the shared engine behind `go`, `go say`, and `go attach`.

    `replay` controls what you see before the live follow begins:
      "all"  — the whole log from line one (you just started it; watch it all)
      "tail" — a little recent context, then only new output (you're checking
               back in; don't re-dump the entire conversation every time)
    Following from the current end is the fix for the old behavior, where every
    `say`/`attach` replayed the whole log from the top."""
    log_p = Path(entry["log_path"])
    pos = 0
    if replay != "all" and log_p.exists():
        with log_p.open("r") as f:
            existing = f.read()
            pos = f.tell()  # follow from here; don't replay everything above
        recent = existing.splitlines()[-15:]
        if any(ln.strip() for ln in recent):
            print(dim("  -- recent --"))
            print("\n".join(recent))
            print(dim("  -- live --"))
    try:
        while True:
            entry = go_state.active_entry(space)
            if log_p.exists():
                with log_p.open("r") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    print(chunk, end="")
            if entry is None:
                print(dim(f"\n(run in '{space}' has finished)"))
                return
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(dim(f"\n(detached — '{space}' keeps running in the background)"))


def cmd_go_attach(cfg, args: str) -> None:
    space = _go_target_space(cfg, args)
    entry = go_state.active_entry(space)
    if entry is None:
        print(yellow(f"nothing running in '{space}'"))
        return
    if not entry.get("log_path"):
        print(yellow(f"'{space}' is running in the foreground elsewhere") + dim(" — nothing to attach to."))
        return
    print(dim(f"— attached to '{space}' (pid {entry['pid']}) — Ctrl-C to detach, it keeps running —"))
    _go_tail(space, entry)


def cmd_go_say(cfg, args: str) -> None:
    active = go_state.list_active()
    parts = args.split(maxsplit=1)
    if parts and parts[0] in active and len(parts) > 1:
        space, text = parts[0], parts[1]
    else:
        space, text = _go_target_space(cfg, ""), args.strip()
    if not text.strip():
        print(dim("usage: go say [space] <text>"))
        return
    entry = active.get(space)
    if entry is None:
        print(yellow(f"nothing running in '{space}'") + dim(" — nothing to say to."))
        return
    if not entry.get("inbox_path"):
        print(yellow(f"'{space}' is running in the foreground elsewhere") + dim(" — no inbox to send to."))
        return
    _go_append_inbox(space, entry, text)
    _go_tail(space, entry)


def _stop_worker(pid: int) -> bool:
    """Kill a detached `go` worker and everything it spawned. The worker leads
    its own session/process group (start_new_session=True), so signalling the
    group takes the agent AND any sandbox/ssh children with it. SIGTERM first
    for a clean exit; if it's wedged (mid tool call, mid model round-trip) and
    won't die in ~1.5s, SIGKILL — a killswitch that doesn't guarantee death
    isn't a killswitch. Returns True once the pid is gone."""
    def _sig(s):
        try:
            os.killpg(pid, s)  # pid == pgid for a session leader
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            try:
                os.kill(pid, s)
            except (ProcessLookupError, PermissionError):
                pass

    def _gone():
        # Reap it if it's our child — otherwise a killed worker lingers as a
        # zombie and os.kill(pid, 0) still reports it "alive", so we could never
        # confirm the kill. Not our child (orphaned by an earlier REPL)? init
        # reaps it; ECHILD just means nothing to reap here.
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        return not pid_alive(pid)

    if not pid:
        return True
    _sig(signal.SIGTERM)
    for _ in range(15):
        if _gone():
            return True
        time.sleep(0.1)
    _sig(signal.SIGKILL)
    for _ in range(10):
        if _gone():
            return True
        time.sleep(0.1)
    return _gone()


def cmd_go_stop(cfg, args: str) -> None:
    """The killswitch. Stop a detached `go` run dead. `go stop` takes down the
    one that's running (or the current space's); `go stop <space>` names one;
    `go stop all` takes down everything. Foreground `run`/`session` aren't
    touched here — those you stop with Ctrl-C, since killing them by pid would
    kill this REPL."""
    workers = {s: e for s, e in go_state.list_active().items() if e.get("kind") == "go"}
    if not workers:
        print(dim("(nothing running to stop)"))
        return
    target = args.strip()
    if target == "all":
        spaces = list(workers)
    elif target:
        if target not in workers:
            print(yellow(f"nothing running in '{target}'"))
            return
        spaces = [target]
    else:
        current = cfg.get("current_project") or DEFAULT_SPACE
        if current in workers:
            spaces = [current]
        elif len(workers) == 1:
            spaces = list(workers)
        else:
            print(yellow("several running — name one or `go stop all`:")
                  + dim(" " + ", ".join(sorted(workers))))
            return
    for space in spaces:
        pid = workers[space].get("pid", 0)
        ok = _stop_worker(pid)
        go_state.clear_entry(space)
        go_state.inbox_path(space).unlink(missing_ok=True)
        go_state.prompt_tmp_path(space).unlink(missing_ok=True)
        if ok:
            print(red(f"stopped '{space}'") + dim(f" (pid {pid})"))
        else:
            print(red(f"could not confirm '{space}' (pid {pid}) died")
                  + dim(" — check `go status`"))


def cmd_go_status(cfg, args: str) -> None:
    active = go_state.list_active()
    if not active:
        print(dim("(nothing running)"))
        return
    for space, entry in sorted(active.items()):
        elapsed = int(time.time() - entry.get("started_epoch", time.time()))
        run_id = entry.get("run_id")
        run_label = f"run {run_id:04d}" if run_id else "run ?   "
        elapsed_label = f"{elapsed // 60}m{elapsed % 60:02d}s"
        kind_field = f"{entry.get('kind', 'go'):<5}"
        space_field = f"{space:<14}"
        pid_label = f"pid {entry['pid']}"
        # Pad the plain text first, then colorize — padding a string that
        # already has ANSI codes in it counts the escape bytes as width and
        # throws the columns off whenever color is on.
        print(f"  {cyan(space_field)}{dim(kind_field)}"
              f"{run_label:<9}{elapsed_label:<8}{dim(pid_label)}")


def cmd_space(cfg, args: str) -> None:
    """A space is one workbench of your work — its own mission, files, and run
    history. You get one automatically; you only need this to keep two unrelated
    efforts apart. `space` lists them, `space new <name>` / `space use <name>`."""
    parts = args.split()
    sub = parts[0] if parts else "list"
    pdir = _projects_dir(cfg)
    if sub == "new" and len(parts) > 1:
        try:
            Project.create(pdir, parts[1])
        except ProjectError as e:
            print(red(e))
            return
        cfg.set("current_project", parts[1], coerce=False)
        cfg.save()
        print(green(f"space '{parts[1]}' created and switched to.") + dim(" Set its brief: `mission edit`"))
    elif sub == "use" and len(parts) > 1:
        try:
            Project.load(pdir, parts[1])
        except ProjectError as e:
            print(red(e))
            return
        cfg.set("current_project", parts[1], coerce=False)
        cfg.save()
        print(green(f"switched to '{parts[1]}'"))
    else:
        current = cfg.get("current_project")
        names = Project.list_names(pdir)
        if not names:
            print(dim("(no spaces yet — `go <something>` starts one)"))
        for n in names:
            print(green("* ") + bold(n) if n == current else "  " + n)


def cmd_gpu(cfg, args: str) -> None:
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "status"
    state = load_gpu_state()

    if sub == "attach":
        if len(parts) > 1:
            try:
                user, host, port = parse_ssh_string(parts[1])
            except SSHError as e:
                print(red(e))
                return
            instance_id = None
        else:
            from hermes.gpu.vast import VastError, running_instances
            try:
                instances = running_instances(cfg.get("vast_api_key", ""))
            except VastError as e:
                print(red(e) + dim("\n(fallback: paste it — `gpu attach ssh -p PORT root@HOST`)"))
                return
            if not instances:
                print(yellow("no running Vast.ai instances found."))
                return
            if len(instances) > 1:
                for i, inst in enumerate(instances):
                    print(f"  {cyan(f'[{i}]')} id={inst['id']} {inst['num_gpus']}x{inst['gpu_name']} ${inst['dph']:.2f}/hr")
                try:
                    pick = int(input("which? "))
                    inst = instances[pick]
                except (ValueError, IndexError, EOFError):
                    print(yellow("cancelled"))
                    return
            else:
                inst = instances[0]
            user, host, port = "root", inst["ssh_host"], int(inst["ssh_port"])
            instance_id = inst["id"]
        ep = SSHEndpoint(host=host, port=port, user=user, ephemeral=True)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        ok, why = ep.check_detail()
        if not ok:
            print(red("ssh check failed") + dim(f" — {why}"))
            return
        ep.run(f"mkdir -p {ep.remote_workspace}")
        isolated = probe_net_isolation(ep)
        print("network isolation: " + (
            green("kernel-level (unshare)") if isolated
            else yellow("regex deny-list only (unshare unavailable in this container)")
        ))
        if state.get("tunnel_pid"):  # don't orphan a tunnel to the old box
            kill_pid(state["tunnel_pid"])
        state = {
            "instance_id": instance_id,
            "host": host, "port": port, "user": user,
            "remote_workspace": ep.remote_workspace,
            "net_isolation": isolated,
            "tunnel_pid": 0, "served_ctx": 0,
        }
        save_gpu_state(state)
        print(green("attached.") + dim(" Next: `gpu serve`"))

    elif sub == "serve":
        from hermes.gpu import provision
        ep = endpoint_from_state(state)
        if ep is None:
            print(yellow("not attached — `gpu attach` first"))
            return
        # Verify the box is actually reachable BEFORE diving into GPU detection —
        # otherwise a dropped/reset SSH link surfaces as a confusing "cannot
        # serve: <gpu detection>" error that looks like a serve/model bug when
        # the real fix is just to re-attach.
        ok, why = ep.check_detail()
        if not ok:
            print(red("box not reachable") + dim(f" — {why}"))
            print(dim("re-attach with `gpu attach` (or retry if it was a transient drop), then `gpu serve`"))
            return
        if "net_isolation" not in state:  # attached with an older version
            state["net_isolation"] = probe_net_isolation(ep)
            save_gpu_state(state)
            ep = endpoint_from_state(state)
        spec = _pick_model(cfg)
        if spec is None:
            print(yellow("cancelled"))
            return
        try:
            gpus = provision.detect_gpus(ep)
            plan = provision.plan_serve(gpus, cfg, spec)
        except provision.ProvisionError as e:
            print(red(f"cannot serve: {e}"))
            return
        print(f"model: {cyan(spec.label)}")
        print(f"GPUs: {cyan(', '.join(plan.gpu_names))} — {plan.total_vram_gb}GB total")
        if spec.server == "vllm":
            detail = f"vLLM · tp={plan.tensor_parallel}, util={plan.gpu_memory_utilization}"
        else:
            detail = f"llama.cpp · {plan.tensor_parallel} GPU(s)"
        print(f"plan: {detail}, context={plan.max_model_len}")
        for note in plan.notes:
            print(yellow(f"note: {note}"))
        try:
            provision.launch(ep, cfg, plan, spec)
        except provision.ProvisionError as e:
            print(red(f"launch failed: {e}"))
            return
        _ensure_tunnel(cfg, state)
        print(dim(f"waiting for the model to come up ({spec.weights_note})..."))
        if provision.wait_ready(ep, cfg, spec):
            state["served_ctx"] = plan.max_model_len
            save_gpu_state(state)
            print(green(f"ready — {spec.label} is listening (context {plan.max_model_len}).")
                  + dim(" Try: run hello"))
        else:
            print(red("timed out.") + dim(" Inspect with: gpu status / `remote tail -n 50 ~/vllm.log`"))

    elif sub == "status":
        if not state.get("host"):
            print(yellow("not attached"))
            return
        box = f"{state['user']}@{state['host']}:{state['port']}"
        print(f"box: {cyan(box)}"
              + (dim(f" (vast id {state['instance_id']})") if state.get("instance_id") else ""))
        print(f"tunnel: pid {state.get('tunnel_pid')} "
              + (green("alive") if pid_alive(state.get("tunnel_pid", 0)) else red("dead")))
        ep = endpoint_from_state(state)
        if _probe_vllm(cfg):
            print("vllm endpoint: " + green("UP"))
        else:
            print("vllm endpoint: " + red("down") + dim(f" — {_vllm_down_hint(ep)}"))
        rc, out, _ = ep.run(
            "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader",
            timeout=20,
        )
        if rc == 0:
            print(out.strip())

    elif sub == "tunnel":
        _ensure_tunnel(cfg, state)
        if _probe_vllm(cfg):
            print("tunnel " + green("up"))
        else:
            ep = endpoint_from_state(state)
            print("tunnel started" + dim(f" — {_vllm_down_hint(ep)}"))

    elif sub in ("up", "resume"):
        iid = state.get("instance_id")
        if not iid or not cfg.get("vast_api_key"):
            print(yellow("no paused Vast instance to resume")
                  + dim(" — `gpu attach` to a running box instead"))
            return
        from hermes.gpu.vast import VastError, get_instance, start_instance
        try:
            start_instance(cfg.get("vast_api_key"), iid)
        except VastError as e:
            print(red(e))
            return
        print(dim(f"resuming Vast instance {iid} — waiting for it to boot..."))
        inst = None
        for _ in range(40):  # ~2 minutes
            try:
                inst = get_instance(cfg.get("vast_api_key"), iid)
            except VastError:
                inst = None
            if inst and inst.get("status") == "running" and inst.get("ssh_host"):
                break
            time.sleep(3)
        else:
            print(red("instance didn't come back up in time")
                  + dim(" — try `gpu up` again, or check the Vast console"))
            return
        # SSH host/port can change across a stop/start — always re-read them.
        user, host, port = "root", inst["ssh_host"], int(inst["ssh_port"])
        ep = SSHEndpoint(host=host, port=port, user=user, ephemeral=True)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        ok, why = ep.check_detail()
        if not ok:
            print(red("ssh check failed after resume") + dim(f" — {why}"))
            return
        ep.run(f"mkdir -p {ep.remote_workspace}")
        isolated = probe_net_isolation(ep)
        if state.get("tunnel_pid"):  # the old tunnel points at the pre-pause host
            kill_pid(state["tunnel_pid"])
        state.update({
            "host": host, "port": port, "user": user,
            "remote_workspace": ep.remote_workspace,
            "net_isolation": isolated, "tunnel_pid": 0, "served_ctx": 0,
        })
        save_gpu_state(state)
        print(green("resumed.") + dim(" The disk persisted, so `gpu serve` skips the "
              "weight download / llama.cpp rebuild. Next: `gpu serve`"))

    elif sub == "down":
        ep = endpoint_from_state(state)
        if ep:
            ep.run("kill $(cat ~/vllm.pid) 2>/dev/null; rm -f ~/vllm.pid")
            print(green("vLLM stopped."))
        if state.get("tunnel_pid"):
            kill_pid(state["tunnel_pid"])
            state["tunnel_pid"] = 0
        if ep:
            ep.close_master()  # don't leave the multiplexed ssh around
        if state.get("instance_id") and cfg.get("vast_api_key"):
            answer = input(
                f"pause Vast instance {state['instance_id']}? stops billing but keeps "
                "the disk, so `gpu up` resumes fast (weights + build intact) [y/N] "
            )
            if answer.strip().lower() == "y":
                from hermes.gpu.vast import VastError, stop_instance
                try:
                    stop_instance(cfg.get("vast_api_key"), state["instance_id"])
                    print(green("instance paused.")
                          + dim(" Resume later with `gpu up`. (To stop paying for the "
                                "disk too, destroy it in the Vast console.)"))
                except VastError as e:
                    print(red(e))
        state["served_ctx"] = 0
        save_gpu_state(state)
    else:
        print(dim("usage: gpu attach [sshstr] | serve | status | tunnel | up | down"))


def cmd_sandbox(cfg, args: str) -> None:
    """The local sandbox: this box (the VPS Hermes runs on) is where the
    air-gapped exec container lives. Nothing to register — `status` shows what
    it can isolate with, `provision` installs the container runtime."""
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "status"
    ep = local_endpoint()

    if sub == "status":
        caps = sandbox_capabilities(ep)
        print("container runtime: " + (
            cyan(caps["runtime"]) if caps["runtime"]
            else yellow("none yet — `sandbox provision` (or it installs on first `sandbox_shell` call)")
        ))
        print("kvm (microVM-capable): " + (
            green("yes") if caps["kvm"]
            else dim("no — running plain containers (expected on a cheap VPS)")
        ))

    elif sub == "provision":
        from hermes.sandbox.provision import SandboxError, ensure_runtime
        try:
            rt = ensure_runtime(ep, on_event=lambda t: print(dim("  " + t)))
            print(green(f"{rt} ready."))
        except SandboxError as e:
            print(red(e))

    else:
        print(dim("usage: sandbox status | provision"))


def cmd_host(cfg, args: str) -> None:
    parts = args.split()
    sub = parts[0] if parts else "list"
    hosts = hosts_mod.load_hosts()

    if sub == "add" and len(parts) >= 3:
        name = parts[1]
        if not hosts_mod.HOST_NAME_RE.match(name):
            print(red("host name must match [A-Za-z0-9_-]{1,32}"))
            return
        # ssh:// form leaves room for a trailing note; a pasted `ssh -p ...`
        # command consumes the whole rest of the line.
        if parts[2].startswith("ssh://"):
            sshstr, note = parts[2], " ".join(parts[3:])
        else:
            sshstr, note = " ".join(parts[2:]), ""
        try:
            user, host, port = parse_ssh_string(sshstr)
        except SSHError as e:
            print(red(e))
            return
        ep = SSHEndpoint(host=host, port=port, user=user)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        if not ep.check():
            print(yellow("warning: ssh check failed — saving anyway (server may be down)"))
        hosts[name] = {"host": host, "port": port, "user": user, "note": note}
        hosts_mod.save_hosts(hosts)
        print(green(f"host '{name}' registered.") + dim(" The agent reaches it with "
              "host_shell/host_read/host_write (reads free, writes ask you)."))

    elif sub == "rm" and len(parts) == 2:
        if hosts.pop(parts[1], None) is None:
            print(red(f"no such host: {parts[1]}"))
            return
        hosts_mod.save_hosts(hosts)
        print(green(f"host '{parts[1]}' removed."))

    elif sub == "list" or not parts:
        if not hosts:
            print(dim("(no managed hosts — `host add <name> ssh://user@host[:port]`)"))
        for name, rec in sorted(hosts.items()):
            note = dim(f"  {rec['note']}") if rec.get("note") else ""
            print(f"  {cyan(name)}  {rec.get('user', 'root')}@{rec['host']}:{rec.get('port', 22)}{note}")
    else:
        print(dim("usage: host add <name> <ssh-string> [note] | list | rm <name>"))


def cmd_directives(cfg, args: str) -> None:
    """Standing instructions distilled from the prompt history (feature 1).
      directives            show directives.md
      directives edit       nano it yourself
      directives reconcile  force a reconciliation pass now
    """
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    sub = args.strip()
    if sub == "edit":
        _edit_file(project.directives_path)
        return
    if sub == "reconcile":
        if not cfg.get("directives_enabled", False):
            print(yellow("directives are off") + dim(" — `config set directives_enabled true` first"))
            return
        if cfg.get("backend") != "mock" and not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(" — `gpu serve` first"))
            return
        from hermes import directives as directives_mod
        from hermes.models import resolve
        spec = resolve(cfg)
        think_re = agent._think_re(spec.think_tags)
        print(dim("reconciling standing instructions from the full history..."))
        text = directives_mod.reconcile(project, make_backend(cfg), cfg, think_re)
        if text is None:
            print(yellow("nothing to reconcile (no history, or the pass failed)."))
        else:
            print(green("directives.md rewritten:\n") + text)
        return
    print(project.read_directives() or dim("(no directives yet — `directives reconcile`"
                                            " or enable `directives_enabled`)"))


def cmd_retrospect(cfg, args: str) -> None:
    """Cross-run self-review (feature 9).
      retrospect            show recent per-run metrics (what the pass reads)
      retrospect now        force a self-review pass immediately
    """
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    if args.strip() == "now":
        if cfg.get("backend") != "mock" and not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(" — `gpu serve` first"))
            return
        from hermes import retrospect as retrospect_mod
        from hermes.models import resolve
        spec = resolve(cfg)
        think_re = agent._think_re(spec.think_tags)
        print(dim("reviewing recent runs against the recorded metrics..."))
        if retrospect_mod.retrospect(project, make_backend(cfg), cfg, think_re):
            print(green("retrospection banked lessons — see `notes` / `skills`."))
        else:
            print(yellow("nothing banked") + dim(" (fewer than 2 measured runs, "
                         "nothing worth changing, or the pass failed)"))
        return
    rows = project.recent_metrics(20)
    if not rows:
        print(dim("(no metrics yet — the harness records runs/NNNN/metrics.json "
                  "per run)"))
        return
    for m in rows:
        line = (f"  run {m.get('run', 0):04d}  turns={m.get('turns', '?'):<3}"
                f" errors={m.get('tool_errors', 0):<3}"
                f" stalls={m.get('stall_nudges', 0)}"
                f" phantom={m.get('phantom_bounces', 0)}"
                f" verify={m.get('verify_bounces', 0)}+{m.get('verify_failures', 0)}"
                f" taint={m.get('tainted_turns', 0)}")
        print(red(line + "  ABORTED") if m.get("aborted") else dim(line))


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def cmd_catalog(cfg, args: str) -> None:
    """The librarian's cards for this space's workspace.
      catalog          show the current card per artifact (what each file is for)
      catalog now      force a catalog pass immediately (needs the endpoint for
                       purpose/tags; the deterministic core runs regardless)
      catalog log      show the full append-only card history (supersessions too)
    """
    from hermes import catalog as catalog_mod
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    sub = args.strip()
    if sub == "now":
        from hermes.models import resolve
        backend = None
        if cfg.get("backend") == "mock" or _probe_vllm(cfg):
            spec = resolve(cfg)
            think_re = agent._think_re(spec.think_tags)
            backend = make_backend(cfg)
        else:
            think_re = None
            print(dim("endpoint down — running the deterministic core only "
                      "(no purpose/tags)."))
        run_id = project.next_run_id() - 1  # attribute to the most recent run
        n = catalog_mod.index(project, backend, cfg, max(run_id, 0), think_re=think_re)
        print(green(f"catalogued {n} artifact(s).") if n else
              dim("nothing new to catalogue."))
        return
    if sub == "log":
        entries = catalog_mod.read_entries(project)
        if not entries:
            print(dim("(no catalog yet — it fills as the agent writes files)"))
        for e in entries:
            sup = f" supersedes {e['supersedes']}" if e.get("supersedes") else ""
            stamp = dim(f"{e.get('ts', '')} r{e.get('run', '?')}")
            kind = e.get("kind", "file")
            path = e.get("path", "?")
            print(f"{stamp} [{kind}] {path}{dim(sup)}")
        return
    view = catalog_mod.digest(project, cfg.get("catalog_digest_chars", 2000))
    if not view:
        print(dim("(no catalog yet — it fills as the agent writes files; "
                  "`catalog now` to build it)"))
        return
    print(view)


def cmd_debug(cfg, args: str) -> None:
    """Diagnostics. `debug prefix` assembles two consecutive packages (with a
    changed runtime status between them) and reports the shared byte prefix — so
    prefix-cache efficiency is measurable, not assumed (feature 5)."""
    from hermes import package
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    sub = (args.split() or ["prefix"])[0]
    if sub != "prefix":
        print(dim("usage: debug prefix"))
        return
    # Two calls that differ only in volatile parts: a changed request and a
    # changed GPU status / host set (the bytes prefix ordering is meant to move).
    env_a = {"gpu_status": "1.2.3.4:8000 (vllm:up)", "managed_hosts": "none",
             "context_window": cfg.get("package_budget_tokens", 10000)}
    env_b = {"gpu_status": "9.9.9.9:8000 (vllm:DOWN)", "managed_hosts": "web=root@9.9.9.9:22",
             "context_window": cfg.get("package_budget_tokens", 10000)}
    msgs_a = package.assemble(project, "probe request one", env_a, cfg)
    msgs_b = package.assemble(project, "a different probe request two", env_b, cfg)
    system_a, system_b = msgs_a[0]["content"], msgs_b[0]["content"]
    text_a = system_a + "\n\x1e\n" + msgs_a[1]["content"]
    text_b = system_b + "\n\x1e\n" + msgs_b[1]["content"]
    shared = _common_prefix_len(text_a, text_b)
    sys_shared = _common_prefix_len(system_a, system_b)
    approx = shared // package.APPROX_CHARS_PER_TOKEN
    print(f"prefix-cache ordering: {cyan('ON' if cfg.get('prefix_cache_order') else 'OFF')}")
    print(f"system prompt: {len(system_a)} chars; identical across the two calls: "
          + (green('yes') if sys_shared == len(system_a) == len(system_b) else
             red(f'no (diverges at char {sys_shared})')))
    print(f"shared package prefix: {cyan(str(shared))} chars (~{approx} tokens)")
    if sys_shared == len(system_a) == len(system_b):
        print(green("  → the full stable header (header + persona + tools + skills "
                    "index) is a byte-identical prefix — cache-friendly."))
    else:
        print(yellow("  → volatile bytes sit inside the header; turn on "
                     "`prefix_cache_order` to move them out."))


def cmd_skills(cfg, args: str) -> None:
    """The agent's reusable how-to notes (feature 3).
      skills               list the index (global + this project)
      skills show <name>   print a skill's full body
      skills edit <name>   nano a skill (creates a global one if new)
    """
    from hermes import skills as skills_mod
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""
    if sub == "show" and name:
        sk = skills_mod.get(project, name)
        print(sk.body.rstrip() if sk else red(f"no such skill: {name}"))
    elif sub == "edit" and name:
        sk = skills_mod.get(project, name)
        if sk is not None:
            _edit_file(sk.path)
        else:
            if not skills_mod.SKILL_NAME_RE.match(name):
                print(red("skill name must match [A-Za-z0-9_-]{1,40}"))
                return
            skills_mod.global_skills_dir().mkdir(parents=True, exist_ok=True)
            path = skills_mod.global_skills_dir() / f"{name}.md"
            if not path.exists():
                path.write_text(f"one-line description of {name}\n\n(procedure)\n")
            _edit_file(path)
    else:
        idx = skills_mod.index(project)
        print(idx or dim("(no skills yet — the agent writes them with write_skill, "
                         "or `skills edit <name>`)"))


def cmd_checkpoint(cfg, args: str) -> None:
    """Project snapshots taken before file-mutating turns (feature 6).
      checkpoint(s)              list snapshots (newest last)
      checkpoint restore <id>    revert the project to a snapshot
    """
    from hermes import checkpoint
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    if sub == "restore" and len(parts) > 1:
        cid = parts[1].strip()
        snaps = {c["id"] for c in checkpoint.list_checkpoints(project)}
        if cid not in snaps:
            print(red(f"no such checkpoint: {cid}") + dim(" — `checkpoint` to list"))
            return
        from hermes.confirm import confirm
        if not confirm(f"revert space '{project.name}' to checkpoint {cid}?",
                       detail=dim("  overwrites workspace/tools/skills/notes/etc. "
                                  "with the snapshot")):
            print(dim("cancelled."))
            return
        if checkpoint.restore(project, cid):
            print(green(f"reverted to {cid}."))
        else:
            print(red("restore failed."))
    else:
        snaps = checkpoint.list_checkpoints(project)
        if not snaps:
            print(dim("(no checkpoints yet — they're taken before file-mutating turns)"))
        for c in snaps:
            label = dim(f"  {c['label']}") if c.get("label") else ""
            print(f"  {cyan(c['id'])}  {dim(c.get('ts', ''))}{label}")


def cmd_config(cfg, args: str) -> None:
    args = args.strip()
    # accept both `config key value` and `config set key value` / `config get key`
    first, _, rest = args.partition(" ")
    if first in ("set", "get"):
        args = rest.strip()
    parts = args.split(maxsplit=1)
    if len(parts) == 2:
        cfg.set(parts[0], parts[1])
        cfg.save()
        print(f"{parts[0]} = {cfg.get(parts[0])}")
    elif len(parts) == 1 and parts[0]:
        print(json.dumps(cfg.get(parts[0]), indent=2))
    else:
        redacted = dict(cfg.data)
        if redacted.get("vast_api_key"):
            redacted["vast_api_key"] = "***"
        print(json.dumps(redacted, indent=2))


def cmd_allow(cfg, args: str) -> None:
    """Manage the persistent http_allow list (hermes/http_policy.py): domains
    (and optionally methods) that never prompt for http_request, tainted or
    not, instead of re-answering the same y/n every run."""
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""
    rules = list(cfg.get("http_allow") or [])
    if sub in ("", "list"):
        if not rules:
            print(dim("no auto-approved domains — `allow add <domain> [METHOD,...]`"))
            return
        for r in rules:
            methods = ",".join(r.get("methods") or ["GET", "HEAD"])
            print(f"  {cyan(r.get('domain', '?'))}  {dim(methods)}")
    elif sub == "add":
        bits = rest.split()
        if not bits:
            print(red("usage: allow add <domain> [METHOD,METHOD,...]"))
            return
        domain = bits[0].lower()
        methods = ([m.strip().upper() for m in bits[1].split(",")]
                   if len(bits) > 1 else ["GET", "HEAD"])
        rules = [r for r in rules if r.get("domain") != domain]
        rules.append({"domain": domain, "methods": methods})
        cfg.set("http_allow", rules)
        cfg.save()
        print(f"auto-approved {cyan(domain)} for {dim(','.join(methods))}")
    elif sub in ("rm", "remove"):
        domain = rest.strip().lower()
        kept = [r for r in rules if r.get("domain") != domain]
        if len(kept) == len(rules):
            print(yellow(f"no rule for {domain}"))
            return
        cfg.set("http_allow", kept)
        cfg.save()
        print(f"removed {domain}")
    else:
        print(red(f"unknown: allow {sub}")
              + dim(" (try: allow / allow add <domain> [methods] / allow rm <domain>)"))


def cmd_info(cfg, what: str, args: str) -> None:
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    if what == "mission":
        if args.strip() == "edit":
            _edit_file(project.mission_path)
        else:
            print(project.read_mission())
    elif what == "strategy":
        # The campaign plan is the LIBRARIAN's, not the operator's — you own the
        # mission, it owns the line that serves it. View-only here: it's set and
        # refined by the librarian's morning pass from the almanac and the runs.
        print(project.read_strategy()
              or dim("(no strategy yet — the librarian sets it on the first "
                     "debate turn with magazine_enabled on)"))
    elif what == "magazine":
        from hermes import magazine as magazine_mod
        print(magazine_mod.read_magazine(project)
              or dim("(no magazine yet — the librarian writes it at the start of "
                     "a debate turn when `magazine_enabled` is on)"))
    elif what == "notes":
        print(project.read_notes() or dim("(no notes)"))
    elif what == "history":
        n = int(args) if args.strip().isdigit() else 20
        for e in project.recent_prompts(n):
            head = f"[{e.get('run', '?'):>4}] {e.get('ts', '')}"
            print(f"{dim(head)}  {e.get('text', '')[:120]}")
    elif what == "summaries":
        n = int(args) if args.strip().isdigit() else 3
        for run_id, text in project.recent_summaries(n):
            print(f"{cyan(f'--- run {run_id:04d} ---')}\n{text}\n")


def cmd_tools(cfg) -> None:
    from hermes.confirm import confirm
    from hermes.tools import build_registry
    project = _current_project(cfg)
    if project is None:
        print(yellow("no space yet") + dim(" — `go <something>` starts one"))
        return
    registry = build_registry(project, cfg, confirm)
    for name in registry.names():
        t = registry._tools[name]
        print(f"  {cyan(name)} {dim(f'[{t.origin}]')}")
    print("\nlibrary (equip via the agent's list_toolbox/equip_tool):")
    for name, t in registry.library_tools().items():
        print(f"  {cyan(name)}: {t.description[:90]}")


# The whole program in a handful of short lines that don't wrap on a phone.
# `help more` opens everything else — still there, just out of the way.
HELP = f"""\
{bold('the essentials')}
{cyan('debate')}         sit at the table and talk it out — a {GO_MAX_RUN_SECONDS // 60}-min conversation, no rush {dim('(alias: d)')}
{cyan('go')} <text>      start a {GO_MAX_RUN_SECONDS // 60}-min session; watch it, steer it, it can ask you back
{cyan('go')}             drop back into what's running
{cyan('stop')}           {bold('killswitch')} — stop it dead now ({cyan('stop all')} for everything)
{cyan('go status')}      what's running now
{cyan('gpu attach')}     get a GPU
{cyan('gpu serve')}      load the model onto it
{cyan('mission')}        the brief it always reads {dim('(mission edit to change)')}
{cyan('help more')}      everything else
{cyan('quit')}           leave
"""

# Everything the essentials view leaves out. Power is all here; it just isn't in
# your face every time you open the program.
HELP_MORE = f"""\
{bold('Starting work')}
{cyan('go')} <text>             background {GO_MAX_RUN_SECONDS // 60}-min session you watch + steer live (survives closing the phone)
{cyan('go')} say [space] <text>  steer a background session (also how you answer when it asks you something)
{cyan('go')} attach [space]     drop into a running session's live view — Ctrl-C to step out
{cyan('go')} stop [space|all]   {bold('killswitch')} — stop a detached run dead {dim('(alias: stop)')}
{cyan('go')} status             list what's running
{cyan('debate')} [text]         sit at the table and reason it out — no "act or finish" pressure, pure talk {dim('(alias: d)')}
{cyan('improve')} [text]        sit at the table to work on Hermes ITSELF — it reads/edits its own source, every edit gated + test-run {dim('(alias: i)')}
{cyan('session')} [text]        sit WITH it in the foreground the whole time instead {dim('(alias: s)')}
{cyan('run')} <text>            one foreground exchange, then back to the prompt {dim('(alias: r)')}

{bold('Where your work lives')}
{cyan('space')} new|use|list    a space is one workbench of work (its own mission, files, run history) {dim('(alias: p)')}
{cyan('mission')} [edit]        the standing brief   ·   {cyan('notes')} / {cyan('history')} [n] / {cyan('summaries')} [n]
{cyan('strategy')}             the librarian's campaign plan (it sets it; you read it)   ·   {cyan('magazine')}  today's brief
{cyan('catalog')} [now|log]     the librarian's index of your workspace — what each file is for
{cyan('checkpoint')} [restore <id>]  snapshots taken before the agent changes files

{bold('The GPU')}
{cyan('gpu')} attach [sshstr] | serve | status | tunnel | down   {dim('(alias: g)')}

{bold('Deeper')}
{cyan('directives')} [edit|reconcile]  ·  {cyan('skills')} [show|edit <name>]  ·  {cyan('retrospect')} [now]  ·  {cyan('tools')}
{cyan('host')} add <name> <sshstr> [note] | list | rm    your real servers
{cyan('sandbox')} status | provision   ·   {cyan('persona')} edit   ·   {cyan('debug')} prefix
{cyan('config')} [key [value]]   ·   {cyan('allow')} [list] | add <domain> [methods] | rm <domain>
"""


def dispatch(cfg, line: str) -> bool:
    """Returns False to exit the REPL."""
    line = line.strip()
    if not line:
        return True
    cmd, _, rest = line.partition(" ")
    cmd = {"r": "run", "p": "project", "g": "gpu", "s": "session",
           "d": "debate", "i": "improve", "exit": "quit", "q": "quit"}.get(cmd, cmd)
    if cmd == "quit":
        return False
    elif cmd == "help":
        print(HELP_MORE if rest.strip() in ("more", "all", "full") else HELP)
    elif cmd == "debate":
        cmd_debate(cfg, rest)
    elif cmd == "improve":
        cmd_improve(cfg, rest)
    elif cmd == "session":
        cmd_session(cfg, rest)
    elif cmd == "go":
        cmd_go(cfg, rest)
    elif cmd in ("stop", "kill"):  # killswitch, reachable without the `go` prefix
        cmd_go_stop(cfg, rest)
    elif cmd == "run":
        cmd_run(cfg, rest)
    elif cmd in ("space", "project"):  # `project` kept as a quiet alias for muscle memory
        cmd_space(cfg, rest)
    elif cmd == "gpu":
        cmd_gpu(cfg, rest)
    elif cmd == "host":
        cmd_host(cfg, rest)
    elif cmd == "sandbox":
        cmd_sandbox(cfg, rest)
    elif cmd == "config":
        cmd_config(cfg, rest)
    elif cmd == "allow":
        cmd_allow(cfg, rest)
    elif cmd == "directives":
        cmd_directives(cfg, rest)
    elif cmd == "skills":
        cmd_skills(cfg, rest)
    elif cmd == "debug":
        cmd_debug(cfg, rest)
    elif cmd in ("checkpoint", "checkpoints"):
        cmd_checkpoint(cfg, rest)
    elif cmd == "retrospect":
        cmd_retrospect(cfg, rest)
    elif cmd == "catalog":
        cmd_catalog(cfg, rest)
    elif cmd in ("mission", "strategy", "magazine", "notes", "history", "summaries"):
        cmd_info(cfg, cmd, rest)
    elif cmd == "tools":
        cmd_tools(cfg)
    elif cmd == "persona":
        _edit_file(persona_path())
    else:
        print(red(f"unknown command: {cmd}") + dim(" (try `help`)"))
    return True


def main() -> None:
    cfg = Config.load()
    cfg.save()  # materialize defaults + persona on first start
    hermes_home().mkdir(parents=True, exist_ok=True)
    print(BANNER)
    print(dim("sit down: ") + cyan("debate") + dim("   ·   send it off: ")
          + cyan("go <what you want done>") + dim("   ·   ") + cyan("help"))

    session = None
    ansi = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import ANSI as ansi
        from prompt_toolkit.history import FileHistory
        session = PromptSession(history=FileHistory(str(hermes_home() / "repl_history")))
    except Exception:
        pass

    def _loop() -> None:
        # The prompt is always the bare `hermes> ` — no space name. You're here
        # to talk to the one guy, not to manage projects; which workbench you're
        # on is a `space` concern, kept out of the face you look at every line.
        prompt_text = f"{magenta('hermes> ')}"
        while True:
            try:
                line = session.prompt(ansi(prompt_text)) if session else input(prompt_text)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            try:
                if not dispatch(cfg, line):
                    break
            except Exception as e:  # the REPL must survive anything
                print(red(f"error: {type(e).__name__}: {e}"))

    # No patch_stdout: `go` runs as a detached subprocess and its output is
    # streamed synchronously by `_go_tail`, so nothing prints into this REPL
    # from a background thread. prompt_toolkit's StdoutProxy would only get in
    # the way — it escapes the raw ANSI in our print()s into literal `^[[2m`
    # gibberish. Printing straight to the terminal renders the colors.
    _loop()
    print(dim("bye."))


if __name__ == "__main__":
    main()
