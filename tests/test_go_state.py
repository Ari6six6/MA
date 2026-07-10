import os

from hermes import go_state


def test_start_and_active_entry_round_trip(home):
    go_state.start_entry("space", os.getpid(), kind="go", log="/tmp/a.log", inbox="/tmp/a.inbox")
    entry = go_state.active_entry("space")
    assert entry is not None
    assert entry["pid"] == os.getpid()
    assert entry["kind"] == "go"
    assert entry["run_id"] is None
    assert entry["log_path"] == "/tmp/a.log"
    assert entry["inbox_path"] == "/tmp/a.inbox"


def test_update_run_id(home):
    go_state.start_entry("space", os.getpid(), kind="go")
    go_state.update_run_id("space", 7)
    assert go_state.active_entry("space")["run_id"] == 7


def test_clear_entry(home):
    go_state.start_entry("space", os.getpid(), kind="run")
    go_state.clear_entry("space")
    assert go_state.active_entry("space") is None


def test_active_entry_prunes_dead_pid(home):
    dead_pid = 99999999  # astronomically unlikely to be a live pid
    go_state.start_entry("ghost", dead_pid, kind="go")
    assert go_state.active_entry("ghost") is None
    assert not go_state.state_path("ghost").exists()


def test_list_active_prunes_and_returns_only_live(home):
    go_state.start_entry("alive", os.getpid(), kind="go")
    go_state.start_entry("dead", 99999999, kind="go")
    active = go_state.list_active()
    assert list(active.keys()) == ["alive"]
    assert not go_state.state_path("dead").exists()


def test_active_entry_missing_file_returns_none(home):
    assert go_state.active_entry("nope") is None
