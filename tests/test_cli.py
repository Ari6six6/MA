import json
import os
import time

from hermes import cli, go_state


def test_ensure_space_autocreates_default(cfg):
    assert cfg.get("current_project") == ""
    project = cli._ensure_space(cfg)
    assert project.name == cli.DEFAULT_SPACE
    assert cfg.get("current_project") == cli.DEFAULT_SPACE


def test_ensure_space_reuses_current_project(cfg, project):
    cfg.set("current_project", project.name, coerce=False)
    cfg.save()
    got = cli._ensure_space(cfg)
    assert got.name == project.name


def test_go_usage_message_on_empty_prompt(cfg, capsys):
    cli.cmd_go(cfg, "   ")
    assert "usage: go" in capsys.readouterr().out


class _FakeProc:
    def __init__(self, pid):
        self.pid = pid


def test_go_spawns_detached_subprocess(cfg, capsys, monkeypatch):
    cfg.set("backend", "mock")
    cfg.save()
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProc(pid=os.getpid())  # active_entry() liveness-checks the pid; use a real live one

    # Isolate this test to argv/state-file wiring — real GPU/sandbox probing
    # (_prepare_run) shells out via subprocess.run, which shares the same
    # module-level Popen we're faking, so skip it rather than fake that too.
    monkeypatch.setattr(cli, "_prepare_run", lambda cfg: (None, None, {}, None))
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    cli.cmd_go(cfg, "hello there")

    argv = captured["argv"]
    assert argv[0] == cli.sys.executable
    assert argv[1] == "-u"
    assert argv[2:4] == ["-m", "hermes.go_worker"]
    assert argv[4] == cli.DEFAULT_SPACE
    assert "hello there" in open(argv[5]).read()  # prompt written to the file the worker reads
    assert captured["kwargs"]["start_new_session"] is True

    entry = go_state.active_entry(cli.DEFAULT_SPACE)
    assert entry is not None
    assert entry["pid"] == os.getpid()
    assert entry["kind"] == "go"
    go_state.clear_entry(cli.DEFAULT_SPACE)

    out = capsys.readouterr().out
    assert "background" in out and "attach" in out and "say" in out and "status" in out


def test_go_reports_busy_via_state_file(cfg, capsys, monkeypatch):
    cfg.set("backend", "mock")
    cfg.save()
    project = cli._ensure_space(cfg)
    go_state.start_entry(project.name, os.getpid(), kind="go")  # os.getpid() is always alive

    def fail_popen(*a, **k):
        raise AssertionError("should not spawn a second background run while one is busy")

    monkeypatch.setattr(cli.subprocess, "Popen", fail_popen)

    cli.cmd_go(cfg, "another one")
    assert "busy" in capsys.readouterr().out
    go_state.clear_entry(project.name)


def test_go_attach_nothing_running(cfg, capsys):
    cli.cmd_go_attach(cfg, "")
    assert "nothing running" in capsys.readouterr().out


def test_go_attach_streams_growing_log_and_detaches_without_killing_it(
    cfg, tmp_path, capsys, monkeypatch,
):
    log = tmp_path / "live.log"
    log.write_text("first line\n")
    go_state.start_entry("space", os.getpid(), kind="go",
                          log=str(log), inbox=str(tmp_path / "x.inbox"))

    calls = {"n": 0}

    def fake_sleep(_secs):
        calls["n"] += 1
        if calls["n"] == 1:
            log.write_text(log.read_text() + "second line\n")  # simulates the worker still writing
        else:
            raise KeyboardInterrupt  # simulates Ctrl-C

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    cli.cmd_go_attach(cfg, "space")

    out = capsys.readouterr().out
    assert "first line" in out and "second line" in out
    assert "detached" in out
    assert go_state.active_entry("space") is not None  # detaching must not kill the background run
    go_state.clear_entry("space")


def test_go_say_nothing_running(cfg, capsys):
    cli.cmd_go_say(cfg, "hello")
    assert "nothing running" in capsys.readouterr().out
    assert not go_state.inbox_path(cli.DEFAULT_SPACE).exists()  # no dangling inbox for a no-op


def test_go_say_appends_to_inbox(cfg, tmp_path, capsys):
    project = cli._ensure_space(cfg)
    inbox = go_state.inbox_path(project.name)
    go_state.start_entry(project.name, os.getpid(), kind="go",
                          log=str(tmp_path / "x.log"), inbox=str(inbox))

    cli.cmd_go_say(cfg, "keep going")

    lines = inbox.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "keep going"
    assert "sent" in capsys.readouterr().out
    go_state.clear_entry(project.name)


def test_go_status_lists_and_prunes(cfg, capsys):
    go_state.start_entry("alive", os.getpid(), kind="go")
    go_state.start_entry("dead", 99999999, kind="go")

    cli.cmd_go_status(cfg, "")

    out = capsys.readouterr().out
    assert "alive" in out
    assert "dead" not in out
    assert not go_state.state_path("dead").exists()
    go_state.clear_entry("alive")


def test_go_status_nothing_running(cfg, capsys):
    cli.cmd_go_status(cfg, "")
    assert "nothing running" in capsys.readouterr().out


def test_run_busy_guard_checks_go_state(cfg, capsys, monkeypatch):
    cfg.set("backend", "mock")
    cfg.save()
    project = cli._ensure_space(cfg)
    go_state.start_entry(project.name, os.getpid(), kind="go")

    def fail_run(*a, **k):
        raise AssertionError("agent.run should not be called while the space is busy")

    monkeypatch.setattr(cli.agent, "run", fail_run)

    cli.cmd_run(cfg, "hi")
    assert "busy" in capsys.readouterr().out
    go_state.clear_entry(project.name)


def test_go_end_to_end_subprocess_smoke(cfg):
    """No Popen mocking: actually spawns `python -u -m hermes.go_worker` and
    waits for it to land, proving the real wiring (argv, log redirection,
    state cleanup) works end to end, not just against the mocked Popen above."""
    cfg.set("backend", "mock")
    cfg.set("stall_nudges", 0)  # one echoed turn is enough to land on a final answer
    cfg.save()

    cli.cmd_go(cfg, "hello there")
    space = cli.DEFAULT_SPACE

    deadline = time.time() + 15
    while go_state.active_entry(space) is not None and time.time() < deadline:
        time.sleep(0.2)

    assert go_state.active_entry(space) is None, "worker did not finish/clean up in time"
    assert "[mock] I received" in go_state.log_path(space).read_text()


def test_remote_server_alive_true_when_pid_running():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "RUNNING\n", "")])
    assert cli._remote_server_alive(ep) is True


def test_remote_server_alive_false_when_no_pid():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(1, "", "")])
    assert cli._remote_server_alive(ep) is False


def test_remote_server_alive_false_without_endpoint():
    assert cli._remote_server_alive(None) is False


def test_vllm_down_hint_never_attached():
    assert "gpu attach" in cli._vllm_down_hint(None)


def test_vllm_down_hint_no_server_launched():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(1, "", "")])
    assert "gpu serve" in cli._vllm_down_hint(ep)


def test_vllm_down_hint_still_warming_up():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "RUNNING\n", "")])
    hint = cli._vllm_down_hint(ep)
    assert "loading" in hint or "warm" in hint
    assert "gpu serve" not in hint
