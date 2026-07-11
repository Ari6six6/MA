import json

from hermes import agent
from hermes.llm import MockBackend


def run_agent(project, cfg, script, confirm=None, gpu=None, sandbox=None,
              inbox_path=None, on_run_started=None, show_thinking=False,
              ask_operator_fn=None):
    backend = MockBackend(script)
    return agent.run(
        project,
        "do the thing",
        cfg,
        backend,
        gpu=gpu,
        sandbox=sandbox,
        env={},
        confirm_fn=confirm or (lambda *a, **k: True),
        inbox_path=inbox_path,
        on_run_started=on_run_started,
        show_thinking=show_thinking,
        ask_operator_fn=ask_operator_fn,
    )


def test_happy_path_with_finish_run(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote out.txt"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote out.txt"
    assert (project.workspace_dir / "out.txt").read_text() == "hello"
    assert (project.runs_dir / "0001" / "summary.md").read_text().strip() == \
        "Did: wrote out.txt"
    assert (project.runs_dir / "0001" / "transcript.jsonl").exists()
    # prompt landed in history
    assert project.recent_prompts(5)[-1]["text"] == "do the thing"


def test_forced_summary_when_model_forgets(project, cfg):
    cfg.set("stall_nudges", 0)  # legacy path: prose is accepted as final immediately
    result = run_agent(project, cfg, [{"text": "all done, bye"}])
    assert not result.aborted
    assert result.summary == "[mock] run done."  # MockBackend obeys forced finish_run
    assert result.final_text == "all done, bye"


def test_stall_nudge_gets_model_to_act(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"text": "I should write out.txt with hello."},  # narrates, no tool call
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote out.txt"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote out.txt"
    assert (project.workspace_dir / "out.txt").read_text() == "hello"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "prose and no tool call" in transcript  # the nudge landed


def test_stall_nudge_flags_repetition(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"text": "I should write the file."},
            {"text": "I should write   the file."},  # same thing, modulo whitespace
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "same message twice" in transcript


def test_stall_nudges_exhausted_accepts_prose(project, cfg):
    result = run_agent(
        project,
        cfg,
        [{"text": "thinking..."}, {"text": "still thinking..."}, {"text": "the answer"}],
    )
    assert not result.aborted
    assert result.final_text == "the answer"  # third prose turn accepted as final
    assert result.summary == "[mock] run done."  # forced finish_run backstop


CODE_REPLY = "Here's the scraper:\n\n```python\nimport requests\nprint('hi')\n```"


def test_phantom_finish_bounced_then_does_real_work(project, cfg):
    # Model pastes code and tries to finish without ever writing a file.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "finish_run", "args": {"summary": "wrote scraper.py"},
             "say": CODE_REPLY},
            # bounced -> now it actually writes the file and finishes for real
            {"tool": "write_file",
             "args": {"path": "workspace/scraper.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote scraper.py"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote scraper.py"
    assert (project.workspace_dir / "scraper.py").read_text() == "print('hi')"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Nobody runs the code in a chat reply" in transcript  # the nudge landed


def test_phantom_finish_allowed_when_file_was_written(project, cfg):
    # Code in the answer is fine when a file was actually written this run.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/scraper.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "done"}, "say": CODE_REPLY},
        ],
    )
    assert not result.aborted
    assert result.summary == "done"  # not bounced
    assert result.turns == 2


def test_phantom_finish_bounce_budget_does_not_loop(project, cfg):
    # If the model insists on finishing with only code (e.g. an explain-only
    # request), the single bounce is spent and prose is accepted — no loop.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "finish_run", "args": {"summary": "example"}, "say": CODE_REPLY},
            {"tool": "finish_run", "args": {"summary": "example, as asked"},
             "say": CODE_REPLY},
        ],
    )
    assert not result.aborted
    assert result.summary == "example, as asked"
    assert result.turns == 2


def test_phantom_guard_ignores_prose_without_code(project, cfg):
    # A normal prose answer with no code fence finishes immediately.
    result = run_agent(
        project,
        cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "I checked the logs; nginx is fine."}],
    )
    assert not result.aborted
    assert result.turns == 1


def test_turn_cap_forces_handoff_summary(project, cfg):
    cfg.set("max_turns", 2)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    assert result.turns == 2
    # cap aborts still get a real model-written summary, not the stub
    assert result.summary == "[mock] run done."


def _metrics(project, run_id=1):
    path = project.runs_dir / f"{run_id:04d}" / "metrics.json"
    return json.loads(path.read_text())


def test_reflect_nudge_on_by_default(project, cfg):
    # On by default (the operator's explicit call): a chain of silent tool-only
    # turns reaching the default streak (4) gets bounced with no cfg.set at all.
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(4)]
    script.append({"tool": "finish_run", "args": {"summary": "done"}})
    result = run_agent(project, cfg, script)
    assert not result.aborted
    assert _metrics(project)["reflect_nudges"] == 1
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Stop and think now" in transcript


def test_reflect_nudge_can_be_disabled(project, cfg):
    cfg.set("reflect_nudge_enabled", False)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(6)]
    script.append({"tool": "finish_run", "args": {"summary": "done"}})
    result = run_agent(project, cfg, script)
    assert not result.aborted
    assert _metrics(project)["reflect_nudges"] == 0
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Stop and think now" not in transcript


def test_reflect_nudge_fires_after_silent_chain(project, cfg):
    cfg.set("reflect_nudge_enabled", True)
    cfg.set("reflect_nudge_every", 3)
    cfg.set("reflect_nudges", 2)
    script = [
        {"tool": "write_note", "args": {"text": "n1"}},  # silent (1)
        {"tool": "write_note", "args": {"text": "n2"}},  # silent (2)
        {"tool": "write_note", "args": {"text": "n3"}},  # silent (3) -> nudge fires
        {"tool": "finish_run", "args": {"summary": "done"}},
    ]
    result = run_agent(project, cfg, script)
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Stop and think now" in transcript
    assert _metrics(project)["reflect_nudges"] == 1


def test_reflect_nudge_streak_resets_on_real_prose(project, cfg):
    cfg.set("reflect_nudge_enabled", True)
    cfg.set("reflect_nudge_every", 3)
    cfg.set("reflect_nudges", 2)
    long_prose = "x" * 60  # >= REFLECT_MIN_PROSE_CHARS, resets the streak
    script = [
        {"tool": "write_note", "args": {"text": "n1"}},  # silent (1)
        {"tool": "write_note", "args": {"text": "n2"}},  # silent (2)
        {"tool": "write_note", "args": {"text": "n3"}, "say": long_prose},  # resets
        {"tool": "write_note", "args": {"text": "n4"}},  # silent (1)
        {"tool": "write_note", "args": {"text": "n5"}},  # silent (2)
        {"tool": "finish_run", "args": {"summary": "done"}},
    ]
    result = run_agent(project, cfg, script)
    assert not result.aborted
    # 5 silent-ish turns total, but the streak never reaches 3 without a reset
    assert _metrics(project)["reflect_nudges"] == 0


def test_reflect_nudge_budget_does_not_loop_forever(project, cfg):
    cfg.set("reflect_nudge_enabled", True)
    cfg.set("reflect_nudge_every", 1)  # fires on every silent turn
    cfg.set("reflect_nudges", 2)  # but capped at 2 for the whole run
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    script.append({"tool": "finish_run", "args": {"summary": "done"}})
    result = run_agent(project, cfg, script)
    assert not result.aborted
    assert _metrics(project)["reflect_nudges"] == 2  # budget exhausted, not unbounded


def test_stub_summary_when_backend_dies(project, cfg):
    class DeadBackend:
        def chat(self, *a, **k):
            from hermes.llm import LLMTransportError
            raise LLMTransportError("vLLM unreachable")

    result = agent.run(project, "do the thing", cfg, DeadBackend(),
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True)
    assert result.aborted
    assert "[auto-stub" in result.summary  # no extra LLM call when transport is down


def test_wrapup_warning_near_turn_cap(project, cfg):
    cfg.set("max_turns", 4)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Only 2 turns remain" in transcript


def test_final_reply_persisted_verbatim(project, cfg):
    from hermes import package
    result = run_agent(
        project,
        cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "Two options: (a) rsync nightly, (b) btrfs snapshots. I lean (b)."}],
    )
    assert result.final_text.startswith("Two options")
    assert (project.runs_dir / "0001" / "final.md").read_text().startswith("Two options")
    # the next run's package carries it verbatim
    user = package.assemble(project, "do option b", {}, cfg)[1]["content"]
    assert "# YOUR LAST REPLY (run 0001" in user
    assert "btrfs snapshots. I lean (b)." in user


def test_circuit_breaker_on_consecutive_errors(project, cfg):
    script = [
        {"tool": "read_file", "args": {"path": "workspace/missing.txt"}}
        for _ in range(5)
    ]
    result = run_agent(project, cfg, script)
    assert result.aborted
    assert result.turns == 3  # breaker trips after 3 consecutive ERROR results


def test_denied_local_shell_feeds_back(project, cfg):
    script = [
        {"tool": "local_shell", "args": {"command": "rm -rf /"}},
        {"tool": "finish_run", "args": {"summary": "operator said no"}},
    ]
    result = run_agent(project, cfg, script, confirm=lambda *a, **k: False)
    assert result.summary == "operator said no"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "DENIED by operator" in transcript


def test_remote_tools_without_gpu(project, cfg):
    cfg.set("gpu_shell", True)  # opt the GPU shell in, then attach no box
    script = [
        {"tool": "remote_shell", "args": {"command": "ls"}},
        {"tool": "finish_run", "args": {"summary": "no gpu"}},
    ]
    result = run_agent(project, cfg, script)
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "no GPU box attached" in transcript
    assert result.summary == "no gpu"


def test_tool_output_echoed_to_operator(project, cfg, capsys):
    # The operator must see the real tool result, not just the model's prose.
    run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/a.txt", "content": "hi"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    out = capsys.readouterr().out
    assert "wrote 2 chars to workspace/a.txt" in out  # real result on screen
    assert "summary recorded" not in out  # finish_run's result stays quiet


def test_echo_result_truncates_long_output(capsys):
    agent._echo_result("\n".join(f"line{i}" for i in range(50)))
    out = capsys.readouterr().out
    assert "line0" in out
    assert "line7" in out
    assert "line8" not in out  # capped at 8 lines
    assert "more line(s)" in out


def test_echo_result_skips_empty(capsys):
    agent._echo_result("   ")
    assert capsys.readouterr().out == ""


SANDBOX = object()  # a non-None stand-in for an attached sandbox host


def test_verification_runs_only_with_a_sandbox(project, cfg):
    # No GPU attached -> no verifier pass, the doer's finish stands as before.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "x=1"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
        gpu=None,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier" not in transcript


def test_verification_passes_lets_run_finish(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "print(2+2)"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            # verifier pass (same backend, next script items):
            {"text": "Ran python m.py, output 4. VERDICT: PASS"},
        ],
        sandbox=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "verifier"' in transcript


def test_verification_fail_bounces_then_doer_fixes(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "import nope"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            {"text": "Ran it, ModuleNotFoundError: nope. VERDICT: FAIL"},  # round 1
            # bounced back to the doer:
            {"tool": "edit_file",
             "args": {"path": "workspace/m.py", "old": "import nope", "new": "x=1"}},
            {"tool": "finish_run", "args": {"summary": "fixed it"}},
            {"text": "Ran it, no error. VERDICT: PASS"},  # round 2
        ],
        sandbox=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "fixed it"
    assert (project.workspace_dir / "m.py").read_text() == "x=1"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "did NOT pass" in transcript  # the failure was fed back


def test_verification_budget_stops_relooping(project, cfg):
    cfg.set("verify_rounds", 1)
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "import nope"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            {"text": "VERDICT: FAIL still broken"},  # round 1, budget now 0
            # doer re-finishes; no budget left -> accepted without another pass
            {"tool": "finish_run", "args": {"summary": "second attempt"}},
        ],
        sandbox=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "second attempt"


def test_verifier_can_use_tools_before_verdict(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            # verifier reads the file, then rules:
            {"tool": "read_file", "args": {"path": "workspace/m.py"}},
            {"text": "Saw print('hi'); ran it. VERDICT: PASS"},
        ],
        sandbox=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier-tool" in transcript


def test_no_verification_for_non_code_runs(project, cfg):
    # A run that wrote no code files (just a note) isn't verified.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_note", "args": {"text": "nginx looked fine"}},
            {"tool": "finish_run", "args": {"summary": "checked, all good"}},
        ],
        sandbox=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "checked, all good"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier" not in transcript


def test_think_blocks_stripped():
    assert agent.strip_think("<think>secret</think>answer") == "answer"
    assert agent.strip_think("<seed:think>x</seed:think>ok") == "ok"
    assert agent.strip_think(None) == ""


def test_narrate_blocks_stripped():
    assert agent.strip_narrate("<narrate>a tale</narrate>answer") == "answer"
    assert agent.strip_narrate(None) == ""
    assert agent.extract_narrate(
        "<narrate>first</narrate>mid<narrate>second</narrate>"
    ) == ["first", "second"]


def test_empty_finish_summary_falls_back_to_real_handoff(project, cfg):
    # finish_run with a whitespace-only summary used to slip past the
    # never-lose-the-handoff fallback (it guarded on `is None`, but the summary
    # stripped to ""). The run should still produce a non-empty summary.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hi"}},
            {"tool": "finish_run", "args": {"summary": "   \n\t "}},
        ],
    )
    assert result.summary.strip() != ""
    assert (project.runs_dir / "0001" / "summary.md").read_text().strip() != ""


def test_inner_voice_logged_but_never_in_context(project, cfg):
    # The model's <think> reasoning is filed to thinking.jsonl and stripped from
    # the visible answer — captured, but it can't steer the run.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/o.txt", "content": "x"},
             "say": "<think>my private reasoning</think>writing the file"},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    tj = project.runs_dir / "0001" / "thinking.jsonl"
    assert tj.exists()
    assert "my private reasoning" in tj.read_text()
    assert "my private reasoning" not in result.final_text
    assert "my private reasoning" not in \
        (project.runs_dir / "0001" / "final.md").read_text()


def test_inner_voice_can_be_disabled(project, cfg):
    cfg.set("inner_voice", False)
    run_agent(
        project,
        cfg,
        [
            {"tool": "finish_run", "args": {"summary": "done"},
             "say": "<think>quiet</think>ok"},
        ],
    )
    assert not (project.runs_dir / "0001" / "thinking.jsonl").exists()


def test_narrator_voice_printed_and_logged(project, cfg, capsys):
    # <narrate> is the outer voice: shown to the operator (unlike <think>) but
    # cut out of the dense reply and filed to its own page.
    result = run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "<narrate>a citizen stirs in the dome</narrate>the file is written"}],
    )
    out = capsys.readouterr().out
    assert "a citizen stirs in the dome" in out
    assert "<narrate>" not in result.final_text
    assert result.final_text == "the file is written"
    nj = project.runs_dir / "0001" / "narration.jsonl"
    assert nj.exists()
    assert "a citizen stirs in the dome" in nj.read_text()


def test_narrator_can_be_disabled(project, cfg, capsys):
    # Off: the aside is discarded, not just unlogged — and never leaks into the
    # dense reply as a raw, unparsed tag.
    cfg.set("narrator_enabled", False)
    result = run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "<narrate>quiet</narrate>the file is written"}],
    )
    out = capsys.readouterr().out
    assert "quiet" not in out
    assert "<narrate>" not in result.final_text
    assert result.final_text == "the file is written"
    assert not (project.runs_dir / "0001" / "narration.jsonl").exists()


def test_on_run_started_callback_fires_with_run_id_and_dir(project, cfg):
    captured = []
    run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"}}],
        on_run_started=lambda run_id, run_dir: captured.append((run_id, run_dir)),
    )
    assert captured == [(1, project.runs_dir / "0001")]


def test_on_run_started_callback_failure_does_not_break_the_run(project, cfg):
    def bad_callback(run_id, run_dir):
        raise RuntimeError("boom")

    result = run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"}}],
        on_run_started=bad_callback,
    )
    assert result.summary == "done"


def test_inbox_message_gets_woven_into_conversation(project, cfg, tmp_path):
    # One echoed turn is enough to land on a final answer, so the operator's
    # text is guaranteed to still be near the tail MockBackend echoes back.
    cfg.set("stall_nudges", 0)
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"text": "actually also check the logs"}) + "\n")

    result = run_agent(project, cfg, [], inbox_path=inbox)

    assert "actually also check the logs" in result.final_text
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "operator"' in transcript
    assert "actually also check the logs" in transcript


def test_inbox_none_is_a_no_op(project, cfg):
    # inbox_path defaults to None (every existing caller) — must not error.
    result = run_agent(project, cfg, [{"tool": "finish_run", "args": {"summary": "done"}}])
    assert result.summary == "done"


def test_ask_operator_is_not_a_builtin(project, cfg):
    # The dialogue tool is added by agent.run only when a live inbox exists; it
    # must never be a plain builtin (that would let a channel-less run offer a
    # tool that can only ever fall back).
    from hermes.tools import build_registry, dialogue

    base = build_registry(project, cfg, lambda *a, **k: True)
    assert "ask_operator" not in base.names()
    assert [t.name for t in dialogue.TOOLS] == ["ask_operator"]


def test_ask_operator_runs_end_to_end_and_falls_back_when_unanswered(
    project, cfg, tmp_path
):
    cfg.set("stall_nudges", 0)
    cfg.set("ask_operator_timeout", 0)  # empty inbox -> immediate fallback, no wait
    inbox = tmp_path / "inbox.jsonl"  # exists as a channel, but no message waiting
    inbox.write_text("")

    result = run_agent(
        project, cfg,
        [
            {"tool": "ask_operator", "args": {"question": "which database?"}},
            {"tool": "finish_run", "args": {"summary": "picked sqlite myself"}},
        ],
        inbox_path=inbox,
    )
    assert not result.aborted
    assert result.summary == "picked sqlite myself"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "ask_operator" in transcript
    assert "No reply" in transcript  # the graceful fallback reached the model


def test_ask_operator_foreground_reads_the_keyboard_reply(project, cfg):
    # A foreground session supplies ask_operator_fn; the agent's question gets a
    # direct keyboard answer that flows back into the run.
    cfg.set("stall_nudges", 0)
    result = run_agent(
        project, cfg,
        [
            {"tool": "ask_operator", "args": {"question": "which db?"}},
            {"tool": "finish_run", "args": {"summary": "used postgres as told"}},
        ],
        ask_operator_fn=lambda q: "postgres, obviously",
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "postgres, obviously" in transcript  # the reply reached the model


def test_show_thinking_prints_inner_voice_when_enabled(project, cfg, capsys):
    run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "<think>reasoning here</think>ok"}],
        show_thinking=True,
    )
    out = capsys.readouterr().out
    assert "[inner voice]" in out
    assert "reasoning here" in out


def test_show_thinking_off_by_default(project, cfg, capsys):
    run_agent(
        project, cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "<think>reasoning here</think>ok"}],
    )
    out = capsys.readouterr().out
    assert "[inner voice]" not in out
