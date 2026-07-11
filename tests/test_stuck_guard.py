"""Feature 12: stuck-loop guard — mechanically blocks repeating an execution
attempt that already failed this run, or one the operator vetoed live."""

import json

from hermes import agent, package
from hermes.llm import MockBackend


def run_agent(project, cfg, script, inbox_path=None):
    backend = MockBackend(script)
    return agent.run(
        project, "do the thing", cfg, backend,
        env={}, confirm_fn=lambda *a, **k: True, inbox_path=inbox_path,
    )


def test_header_rule_present_only_when_enabled(project, cfg):
    off = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "Stuck-loop rule" not in off
    cfg.set("stuck_guard_enabled", True)
    on = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "Stuck-loop rule" in on


def test_disabled_by_default_repeats_run_freely(project, cfg):
    # Same failing command three times in a row — with the guard off, every
    # attempt actually dispatches (and fails on its own merits), no DENIED
    # from the guard itself.
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "python bad.py"}},
            {"tool": "local_shell", "args": {"command": "python bad.py"}},
            {"tool": "local_shell", "args": {"command": "python bad.py"}},
            {"tool": "finish_run", "args": {"summary": "gave up"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "stuck guard" not in transcript


def test_repeat_of_a_failed_attempt_is_denied(project, cfg):
    cfg.set("stuck_guard_enabled", True)
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "already failed 1 time(s) this run" in transcript


def test_near_duplicate_command_still_caught(project, cfg):
    # Digits blurred, whitespace collapsed — a reworded retry of the same idea
    # is still the same fingerprint, matching the observed "small variations
    # of the same broken approach" pattern.
    cfg.set("stuck_guard_enabled", True)
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "python calc.py 12345"}},
            {"tool": "local_shell", "args": {"command": "python   calc.py 99999"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "DENIED (stuck guard)" in transcript


def test_different_approach_is_never_blocked(project, cfg):
    cfg.set("stuck_guard_enabled", True)
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "local_shell", "args": {"command": "echo something else entirely"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    # The header rule (present whenever the guard is on) mentions the phrase
    # "DENIED (stuck guard)" generically — check for the per-call denial
    # text instead, which only appears when a real block actually fired.
    assert "already failed" not in transcript
    assert "will not run again this run" not in transcript


def test_escalation_nudge_fires_once_after_threshold(project, cfg):
    cfg.set("stuck_guard_enabled", True)
    cfg.set("stuck_escalate_blocks", 1)
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "local_shell", "args": {"command": "false"}},  # 1st block -> escalation
            {"tool": "local_shell", "args": {"command": "false"}},  # 2nd block -> no repeat
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert transcript.count("mechanically blocked more than once") == 1


def test_live_veto_blocks_instantly_with_no_prior_failure(project, cfg, monkeypatch):
    # This is the direct fix for "I told him to stop and he did it anyway": a
    # veto lands the moment it's drained, with zero failure history needed —
    # threshold is set high so the ordinary failure-count path could NOT have
    # blocked the second attempt on its own.
    cfg.set("stuck_guard_enabled", True)
    cfg.set("stuck_repeat_threshold", 5)
    cfg.set("stall_nudges", 0)

    calls = {"n": 0}

    def fake_drain(path):
        calls["n"] += 1
        return ["veto"] if calls["n"] == 2 else []

    monkeypatch.setattr(agent.go_state, "drain_inbox", fake_drain)

    result = agent.run(
        project, "do the thing", cfg,
        MockBackend([
            {"tool": "local_shell", "args": {"command": "python risky_math.py"}},
            {"tool": "local_shell", "args": {"command": "python risky_math.py"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        env={}, confirm_fn=lambda *a, **k: True, inbox_path="unused",
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "operator VETO" in transcript
    assert "DENIED (stuck guard)" in transcript
    assert "your operator explicitly vetoed" in transcript


def test_metrics_record_blocked_repeats(project, cfg):
    cfg.set("stuck_guard_enabled", True)
    result = run_agent(
        project, cfg,
        [
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "local_shell", "args": {"command": "false"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    metrics = json.loads((project.runs_dir / "0001" / "metrics.json").read_text())
    assert metrics["blocked_repeats"] == 1
