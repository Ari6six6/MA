"""Phase 2: the librarian's three heavy end-of-run passes (retrospection, the
catalog, the almanac) run in a daemon thread so an interactive caller gets the
prompt back the instant a run finishes. The next run — and process exit — join
before anything reads the files those passes write.

These tests drive agent.run directly with a MockBackend and stub out the pass
body (_librarian_passes), so they pin the *scheduling* contract — off-thread
execution, the join barrier, main-thread-only announcements — without depending
on which passes happen to be due.
"""

import threading

from hermes import agent
from hermes.llm import MockBackend


def _finish(summary="done"):
    return [{"tool": "finish_run", "args": {"summary": summary}}]


def _run(project, cfg, *, background):
    return agent.run(
        project, "do the thing", cfg, MockBackend(_finish()),
        env={}, confirm_fn=lambda *a, **k: True,
        background_housekeeping=background,
    )


def test_background_run_defers_passes_to_a_worker_thread(project, cfg, monkeypatch, capsys):
    seen = {"thread": None, "done": threading.Event()}

    def fake_passes(*a, **k):
        seen["thread"] = threading.current_thread().name
        seen["done"].set()
        return [agent.magenta("  (catalog — 2 artifact card(s) updated)")]

    monkeypatch.setattr(agent, "_librarian_passes", fake_passes)

    _run(project, cfg, background=True)

    # The passes ran off the caller's thread...
    assert seen["done"].wait(5), "worker never ran"
    assert seen["thread"] and seen["thread"].startswith("hermes-housekeeping")
    # ...a handle is left pending for the next run / exit to join...
    assert agent._PENDING.thread is not None
    # ...and nothing was announced on the main thread yet (the worker is silent).
    assert "artifact card" not in capsys.readouterr().out

    # flush joins the worker and surfaces its announcement from the main thread.
    agent.flush_housekeeping()
    assert agent._PENDING.thread is None
    assert "artifact card" in capsys.readouterr().out


def test_next_run_joins_previous_housekeeping_before_assembling(project, cfg, monkeypatch):
    order = []
    gate = threading.Event()

    def slow_passes(*a, **k):
        gate.wait(5)            # hold the worker open until the test releases it
        order.append("housekeeping-finished")
        return []

    real_assemble = agent.package.assemble

    def tracking_assemble(*a, **k):
        order.append("assemble")
        return real_assemble(*a, **k)

    monkeypatch.setattr(agent, "_librarian_passes", slow_passes)
    monkeypatch.setattr(agent.package, "assemble", tracking_assemble)

    _run(project, cfg, background=True)   # spawns the (blocked) worker
    assert agent._PENDING.thread is not None

    gate.set()                            # let the worker finish
    _run(project, cfg, background=True)    # its first act must be to join run 1's worker

    # The barrier held: run 1's housekeeping finished before run 2 *assembled*.
    # (Run 1 assembles too, at order[0] — compare against run 2's assemble.)
    run1_housekeeping = order.index("housekeeping-finished")
    run2_assemble = [i for i, x in enumerate(order) if x == "assemble"][1]
    assert run1_housekeeping < run2_assemble
    agent.flush_housekeeping()


def test_flush_is_a_noop_when_nothing_pending(capsys):
    agent._PENDING.thread = None
    agent.flush_housekeeping()            # must not raise or print
    assert capsys.readouterr().out == ""


def test_synchronous_default_runs_passes_inline_and_prints(project, cfg, monkeypatch, capsys):
    called = {"thread": None}

    def fake_passes(*a, **k):
        called["thread"] = threading.current_thread().name
        return [agent.magenta("  (retrospection — banked lessons from recent runs)")]

    monkeypatch.setattr(agent, "_librarian_passes", fake_passes)

    _run(project, cfg, background=False)

    # Default path: passes ran on the caller's (main) thread, printed inline,
    # and left nothing pending.
    assert called["thread"] == threading.main_thread().name
    assert agent._PENDING.thread is None
    assert "banked lessons" in capsys.readouterr().out
