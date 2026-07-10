import os
from pathlib import Path

from hermes import cli, go_state, go_worker
from hermes.llm import MockBackend
from hermes.project import Project


def test_main_runs_the_prompt_and_cleans_up(home, cfg, monkeypatch, capsys):
    cfg.set("backend", "mock")
    cfg.set("stall_nudges", 0)
    cfg.save()

    projects_dir = Path(cfg.get("projects_dir"))
    Project.create(projects_dir, "space")

    prompt_file = go_state.prompt_tmp_path("space")
    prompt_file.write_text("hello from the worker test")

    monkeypatch.setattr(
        cli, "_prepare_run",
        lambda cfg: (None, None, {}, MockBackend()),
    )
    go_state.start_entry("space", os.getpid(), kind="go",
                          log=str(go_state.log_path("space")),
                          inbox=str(go_state.inbox_path("space")))
    monkeypatch.setattr("sys.argv", ["go_worker", "space", str(prompt_file)])

    go_worker.main()

    assert not prompt_file.exists()  # consumed
    assert go_state.active_entry("space") is None  # cleaned up in finally
    assert not go_state.inbox_path("space").exists()

    project = Project.load(projects_dir, "space")
    assert (project.runs_dir / "0001" / "summary.md").exists()
    out = capsys.readouterr().out
    assert "hello from the worker test" in out  # prompt reached the model (mock echo)
    assert "done" in out


def test_main_aborts_cleanly_when_backend_unreachable(home, cfg, monkeypatch, capsys):
    cfg.save()  # go_worker.main() reads a fresh Config.load(), so this must be on disk
    projects_dir = Path(cfg.get("projects_dir"))
    Project.create(projects_dir, "space")
    prompt_file = go_state.prompt_tmp_path("space")
    prompt_file.write_text("hi")

    monkeypatch.setattr(cli, "_prepare_run", lambda cfg: None)  # simulate unreachable vLLM
    go_state.start_entry("space", os.getpid(), kind="go")
    monkeypatch.setattr("sys.argv", ["go_worker", "space", str(prompt_file)])

    go_worker.main()

    assert go_state.active_entry("space") is None  # still cleaned up
    assert "not reachable" in capsys.readouterr().out
