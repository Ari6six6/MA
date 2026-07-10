import threading

from hermes import cli


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


def test_go_runs_in_background_and_prints_final_answer(cfg, capsys):
    cfg.set("backend", "mock")
    cfg.set("stall_nudges", 0)  # one echoed turn is enough to land on a final answer
    cfg.save()

    cli.cmd_go(cfg, "hello there")

    threads = [t for t in threading.enumerate() if t.name.startswith("go-")]
    assert threads, "expected a background go- thread"
    for t in threads:
        t.join(timeout=5)

    out = capsys.readouterr().out
    assert f"working in '{cli.DEFAULT_SPACE}'" in out
    assert "done" in out
    assert "[mock] I received" in out


def test_go_reports_busy_instead_of_double_running(cfg, capsys):
    cfg.set("backend", "mock")
    cfg.save()
    proj = cli._ensure_space(cfg)
    lock = cli._run_lock(proj.name)
    cli._ACTIVE_GO[proj.name] = 7
    lock.acquire()
    try:
        cli.cmd_go(cfg, "another one")
        out = capsys.readouterr().out
        assert "still working" in out
        assert not [t for t in threading.enumerate() if t.name.startswith("go-")]
    finally:
        lock.release()
        cli._ACTIVE_GO.pop(proj.name, None)


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
