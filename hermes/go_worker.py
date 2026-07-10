"""The detached `go` subprocess entrypoint: `python -u -m hermes.go_worker
<space> <prompt-file>`. Runs one agent.run() call unattended, narrating in
full (not quiet) to its stdout — which `cmd_go` redirects to the space's log
file, so `go attach` can tail it live — and cleans up its own state/inbox on
the way out, however the run ends.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hermes import agent, cli, go_state
from hermes.config import Config
from hermes.project import Project
from hermes.ui import red


def main() -> None:
    space, prompt_file = sys.argv[1], Path(sys.argv[2])
    prompt = prompt_file.read_text()
    prompt_file.unlink(missing_ok=True)  # consume-once

    cfg = Config.load()
    project = Project.load(Path(cfg.get("projects_dir")).expanduser(), space)

    try:
        prepared = cli._prepare_run(cfg)
        if prepared is None:
            print(red("vLLM endpoint not reachable — aborting background run."))
            return
        gpu, sandbox, env, backend = prepared

        result = agent.run(
            project, prompt, cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
            confirm_fn=lambda action, detail="", viewable=None: True,  # unattended: nothing to stall on
            quiet=False,  # narration streams to the redirected log file
            max_run_seconds=go_state.GO_MAX_RUN_SECONDS,  # hard ceiling, never extended by `go say`
            inbox_path=go_state.inbox_path(space),
            on_run_started=lambda run_id, _run_dir: go_state.update_run_id(space, run_id),
            show_thinking=True,  # inner voice visible whenever attached
        )
        status = "aborted" if result.aborted else "done"
        print(f"\n[{space}] {status} — run {result.run_id:04d}, {result.turns} turn(s)")
    finally:
        go_state.clear_entry(space)
        go_state.inbox_path(space).unlink(missing_ok=True)  # don't leak unread msgs into the next `go`


if __name__ == "__main__":
    main()
