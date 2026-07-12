"""Feature 4: subagent delegation."""

import json

from hermes import agent, subagent
from hermes.llm import ChatResult, MockBackend, ToolCall
from hermes.tools import build_registry
from hermes.tools.base import ToolContext


def _parent_ctx(project, cfg, backend, confirm=None, depth=0):
    cfg.set("delegate_enabled", True)
    reg = build_registry(project, cfg, confirm or (lambda *a, **k: True))
    ctx = ToolContext(
        project=project, cfg=cfg, confirm=confirm or (lambda *a, **k: True),
        backend=backend, think_re=None, depth=depth,
    )
    ctx.registry = reg
    return ctx


class ScriptBackend:
    """Emits a fixed sequence of turns; each item is a ChatResult factory."""

    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, tool_choice=None):
        if self.turns:
            return self.turns.pop(0)()
        return ChatResult(content="(script exhausted)")


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


def _say(text):
    return lambda: ChatResult(content=text)


def test_child_runs_subset_and_returns_summary(project, cfg):
    # Child writes a file then finishes; only the summary comes back.
    backend = ScriptBackend([
        _call("write_file", {"path": "workspace/child.txt", "content": "hi"}),
        _call("finish_run", {"summary": "wrote child.txt, all good"}),
    ])
    ctx = _parent_ctx(project, cfg, backend)
    out = subagent.run_child(ctx, "write a file", ["write_file"], cfg)
    assert out == "wrote child.txt, all good"
    assert (project.workspace_dir / "child.txt").read_text() == "hi"


def test_child_cannot_exceed_parent_tools(project, cfg):
    # Ask for a tool the parent doesn't have -> silently dropped; child registry
    # is a strict subset. local_shell IS a parent tool, so it's grantable; a
    # made-up name is not.
    backend = ScriptBackend([_call("finish_run", {"summary": "done"})])
    ctx = _parent_ctx(project, cfg, backend)
    reg = subagent._child_registry(ctx, ["write_file", "not_a_real_tool"],
                                    depth=1, max_depth=1, cfg=cfg)
    assert "write_file" in reg.names()
    assert "not_a_real_tool" not in reg.names()
    assert "finish_run" in reg.names()


def test_child_gated_tool_still_asks_operator(project, cfg):
    # local_shell is owner-confirmed. A DENY inside the child must be honoured.
    calls = {"n": 0}

    def deny(*a, **k):
        calls["n"] += 1
        return False

    backend = ScriptBackend([
        _call("local_shell", {"command": "echo hi"}),  # will be DENIED
        _call("finish_run", {"summary": "operator blocked the shell"}),
    ])
    ctx = _parent_ctx(project, cfg, backend, confirm=deny)
    out = subagent.run_child(ctx, "run a shell cmd", ["local_shell"], cfg)
    assert calls["n"] == 1  # the confirm flow fired inside the child
    assert out == "operator blocked the shell"


def test_depth_cap_blocks_grandchildren(project, cfg):
    # A child (depth 1) with default max_depth 1 must not get a delegate tool.
    backend = ScriptBackend([_call("finish_run", {"summary": "done"})])
    ctx = _parent_ctx(project, cfg, backend)
    reg = subagent._child_registry(ctx, ["delegate"], depth=1, max_depth=1, cfg=cfg)
    assert "delegate" not in reg.names()


def test_delegate_tool_depth_guard(project, cfg):
    backend = ScriptBackend([])
    ctx = _parent_ctx(project, cfg, backend, depth=1)  # already at the cap
    out = ctx.registry.dispatch("delegate", json.dumps({"brief": "x"}), ctx)
    assert out.startswith("ERROR: delegation depth cap")


def test_cap_out_returns_structured_progress(project, cfg):
    cfg.set("delegate_max_turns", 2)
    # Child never finishes: two working turns, then the cap.
    backend = ScriptBackend([
        _call("write_note", {"text": "step 1"}),
        _call("write_note", {"text": "step 2"}),
    ])
    ctx = _parent_ctx(project, cfg, backend)
    out = subagent.run_child(ctx, "endless task", ["write_note"], cfg)
    assert "[sub-agent stopped: turn cap reached]" in out
    assert "write_note" in out


def test_delegate_disabled_returns_error(project, cfg):
    cfg.set("delegate_enabled", False)  # on by default now; exercise the off path
    reg = build_registry(project, cfg, lambda *a, **k: True)
    assert "delegate" not in reg.names()


# ---- the Village: embodied delegation ----------------------------------------
class SandboxFakeEp:
    """A fake docker daemon answering by command shape; records every call."""

    def __init__(self):
        self.calls = []

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        c = command
        if "command -v docker" in c:
            return (0, "docker", "")
        if "network ls" in c:
            return (0, "", "")            # network absent -> create
        if "network create" in c or "network rm" in c:
            return (0, "netid", "")
        if " ps -a" in c or "docker ps" in c:
            return (0, "", "")            # container absent -> create
        if " run -d " in c:
            return (0, "cid", "")
        if " exec -w " in c:
            return (0, "ran in body", "")
        if " logs " in c:
            return (0, "body logs", "")
        if " inspect " in c:
            return (0, '[{"Name":"x"}]', "")
        return (0, "", "")


def _village_ctx(project, cfg, backend, sandbox, village_on=True):
    cfg.set("delegate_enabled", True)
    cfg.set("village_enabled", village_on)
    reg = build_registry(project, cfg, lambda *a, **k: True)
    ctx = ToolContext(
        project=project, cfg=cfg, confirm=lambda *a, **k: True,
        backend=backend, think_re=None, depth=0, sandbox=sandbox,
    )
    ctx.registry = reg
    return ctx


def test_embodied_child_runs_in_its_own_body(project, cfg):
    import re
    sandbox = SandboxFakeEp()
    backend = ScriptBackend([
        _call("sandbox_shell", {"command": "echo hi"}),
        _call("finish_run", {"summary": "did it in my body"}),
    ])
    ctx = _village_ctx(project, cfg, backend, sandbox)
    out = subagent.run_child(ctx, "run echo", ["sandbox_shell"], cfg, role="scraper")
    assert out == "did it in my body"
    runs = [c for c in sandbox.calls if " run -d " in c]
    assert runs and "--label hermes.citizen=1" in runs[0]
    citizen = re.search(r"--name (\S+)", runs[0]).group(1)
    assert "scraper" in citizen  # the role named the body
    # the child's shell ran in ITS body, not the shared exec box
    execs = [c for c in sandbox.calls if " exec -w " in c]
    assert execs and citizen in execs[0] and "hermes-exec-" not in execs[0]
    # harvested (logs) then removed (rm) — in that order
    kinds = [("logs" if " logs " in c else "rm" if " rm -f " in c else "")
             for c in sandbox.calls]
    assert "logs" in kinds and "rm" in kinds
    assert kinds.index("logs") < kinds.index("rm")
    assert (project.population_dir / citizen / "report.md").exists()


def test_embodied_child_is_harvested_even_on_cap(project, cfg):
    # A child that never finishes still gets its body carried up the mountain.
    cfg.set("delegate_max_turns", 1)
    sandbox = SandboxFakeEp()
    backend = ScriptBackend([_call("sandbox_shell", {"command": "loop"})])
    ctx = _village_ctx(project, cfg, backend, sandbox)
    out = subagent.run_child(ctx, "endless", ["sandbox_shell"], cfg)
    assert "sub-agent stopped" in out
    assert any(" logs " in c for c in sandbox.calls)   # harvested despite no finish
    assert any(" rm -f " in c for c in sandbox.calls)


def test_village_narrates_birth_and_harvest(project, cfg, capsys):
    # Even if the model never says a word, the harness itself narrates the
    # lifecycle events it already knows happened — birth and harvest.
    sandbox = SandboxFakeEp()
    backend = ScriptBackend([
        _call("sandbox_shell", {"command": "echo hi"}),
        _call("finish_run", {"summary": "did it in my body"}),
    ])
    ctx = _village_ctx(project, cfg, backend, sandbox)
    subagent.run_child(ctx, "run echo", ["sandbox_shell"], cfg, role="scraper")
    out = capsys.readouterr().out
    assert "draws its first breath" in out
    assert "watch ends" in out


def test_village_narration_can_be_disabled(project, cfg, capsys):
    cfg.set("narrator_enabled", False)
    sandbox = SandboxFakeEp()
    backend = ScriptBackend([
        _call("sandbox_shell", {"command": "echo hi"}),
        _call("finish_run", {"summary": "did it in my body"}),
    ])
    ctx = _village_ctx(project, cfg, backend, sandbox)
    subagent.run_child(ctx, "run echo", ["sandbox_shell"], cfg, role="scraper")
    out = capsys.readouterr().out
    assert "draws its first breath" not in out
    assert "watch ends" not in out


def test_embodied_child_narrate_tag_shown_and_stripped(project, cfg, capsys):
    # A narrate aside plus plain prose, no tool call: the child returns its
    # last spoken text as the conclusion (the "stops without finishing still
    # owes a conclusion" path), which must come back with the tag gone.
    backend = ScriptBackend([
        lambda: ChatResult(
            content="<narrate>a citizen leans into its first task</narrate>"
                    "the shell ran clean",
        ),
    ])
    sandbox = SandboxFakeEp()
    ctx = _village_ctx(project, cfg, backend, sandbox)
    conclusion = subagent.run_child(ctx, "run echo", ["sandbox_shell"], cfg, role="scraper")
    printed = capsys.readouterr().out
    assert "a citizen leans into its first task" in printed
    assert "<narrate>" not in conclusion
    assert conclusion == "the shell ran clean"


def test_village_off_delegation_stays_in_process(project, cfg):
    sandbox = SandboxFakeEp()
    backend = ScriptBackend([
        _call("write_note", {"text": "x"}),
        _call("finish_run", {"summary": "done in-process"}),
    ])
    ctx = _village_ctx(project, cfg, backend, sandbox, village_on=False)
    out = subagent.run_child(ctx, "note it", ["write_note"], cfg)
    assert out == "done in-process"
    assert not any(" run -d " in c for c in sandbox.calls)  # no body born
    assert not project.population_dir.exists()


# ---- end to end: parent context grows by only brief + summary ----------------
class ParentWithDelegate:
    """Parent delegates once, then finishes. The child's own turns are served by
    the same backend (interleaved), but must NOT appear in the parent's messages."""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, tool_choice=None):
        self.n += 1
        # Distinguish parent vs child by the system prompt (child = subagent.md).
        is_child = messages[0]["content"].startswith("You are a SUB-AGENT")
        if is_child:
            if self.n_child_step == 0:
                self.n_child_step = 1
                return ChatResult(content=None, tool_calls=[
                    ToolCall("cc", "write_note", json.dumps({"text": "SPAMMY CHILD DETAIL"}))])
            return ChatResult(content=None, tool_calls=[
                ToolCall("cf", "finish_run",
                         json.dumps({"summary": "CHILD CONCLUSION: found 3 places"}))])
        # parent
        if self.n == 1:
            self.n_child_step = 0
            return ChatResult(content=None, tool_calls=[
                ToolCall("pd", "delegate", json.dumps({
                    "brief": "search the repo", "allowed_tools": ["write_note"]}))])
        return ChatResult(content=None, tool_calls=[
            ToolCall("pf", "finish_run", json.dumps({"summary": "parent done"}))])


def test_parent_context_grows_by_only_brief_and_summary(project, cfg):
    cfg.set("delegate_enabled", True)
    result = agent.run(project, "do it", cfg, ParentWithDelegate(),
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True)
    assert not result.aborted
    assert result.summary == "parent done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    # the child's conclusion reached the parent as the delegate tool result...
    assert "CHILD CONCLUSION: found 3 places" in transcript
    # ...but the child's spammy intermediate step is only in the child's own
    # (logged) trace, never spliced into the parent's message list. Assert the
    # parent's delegate tool RESULT is the conclusion, not the spam.
    lines = [json.loads(l) for l in transcript.splitlines()]
    delegate_results = [
        e for e in lines
        if e.get("role") == "tool" and "CHILD CONCLUSION" in e.get("content", "")
    ]
    assert delegate_results  # parent saw the conclusion as a tool result
    parent_tool_spam = [
        e for e in lines
        if e.get("role") == "tool" and "SPAMMY CHILD DETAIL" in e.get("content", "")
    ]
    assert not parent_tool_spam  # child's note result never entered parent context
