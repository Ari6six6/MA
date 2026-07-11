"""Feature 14 — The almanac: cross-project hypotheses about outcomes, and the
librarian's end-of-run pass that reasons over this run's expected-vs-actual
ledger to bank them."""

import json

from hermes import agent, almanac, catalog, package
from hermes.llm import MockBackend
from hermes.tools import build_registry


def run_agent(project, cfg, script, **kw):
    backend = MockBackend(script)
    return agent.run(
        project, "do the thing", cfg, backend,
        env={}, confirm_fn=lambda *a, **k: True, **kw,
    )


# -- hermes/almanac.py: the global store --------------------------------------

def test_write_entry_requires_claim_and_hypothesis(home):
    assert "ERROR" in almanac.write_entry("topic", "", "why")
    assert "ERROR" in almanac.write_entry("topic", "claim", "")


def test_write_entry_validates_topic_slug(home):
    result = almanac.write_entry("Not A Slug!", "claim", "hypothesis")
    assert "ERROR" in result
    assert almanac.get("Not A Slug!") is None


def test_write_and_get_roundtrip(home):
    result = almanac.write_entry(
        "vast-ssh-timeout", "vast.ai SSH refuses connections for ~30s after boot",
        "The instance's sshd isn't up yet; connecting immediately races it.",
        expected="ssh connects immediately", actual="connection refused",
        evidence="vast.ai forum thread on cold-boot timing",
        confidence="medium", project="scraper-proj", run=3,
    )
    assert "banked" in result
    entry = almanac.get("vast-ssh-timeout")
    assert entry is not None
    assert entry["claim"].startswith("vast.ai SSH refuses")
    assert entry["confidence"] == "medium"
    assert entry["project"] == "scraper-proj"


def test_rewriting_same_topic_refines_not_duplicates(home):
    almanac.write_entry("t", "first claim", "first theory")
    result = almanac.write_entry("t", "sharper claim", "better theory")
    assert "refined" in result
    assert len(almanac.current_entries()) == 1  # not two live cards
    assert almanac.get("t")["claim"] == "sharper claim"
    assert len(almanac.read_entries()) == 2  # the superseded card is still on disk


def test_index_lists_topics_and_claims(home):
    almanac.write_entry("a", "claim a", "why a")
    almanac.write_entry("b", "claim b", "why b", confidence="high")
    idx = almanac.index()
    assert "`a`" in idx and "claim a" in idx
    assert "`b` (high)" in idx and "claim b" in idx


def test_index_empty_when_no_entries(home):
    assert almanac.index() == ""


def test_index_truncates_to_budget(home):
    for i in range(50):
        almanac.write_entry(f"topic-{i}", "x" * 100, "why")
    idx = almanac.index(max_chars=300)
    assert len(idx) < 500  # budget respected (plus the "N more" tail line)
    assert "more" in idx


# -- does a banked entry actually reach the agent's next run? ----------------

def test_almanac_index_in_system_prompt_only_when_enabled(project, cfg, home):
    almanac.write_entry("bad-py-crash", "bad.py crashes on a bad import",
                         "the sandbox result showed a traceback pointing at a missing dep")
    cfg.set("almanac_enabled", False)
    off = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "bad.py crashes on a bad import" not in off
    assert "## Almanac" not in off
    cfg.set("almanac_enabled", True)
    on = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "## Almanac" in on
    assert "`bad-py-crash`" in on
    assert "bad.py crashes on a bad import" in on


def test_almanac_tool_registered_only_when_enabled(project, cfg):
    cfg.set("almanac_enabled", False)
    reg = build_registry(project, cfg, lambda *a, **k: True)
    assert "load_almanac" not in reg.names()
    assert "almanac_note" not in reg.names()  # never in the doer's own registry
    cfg.set("almanac_enabled", True)
    reg = build_registry(project, cfg, lambda *a, **k: True)
    assert "load_almanac" in reg.names()
    assert "almanac_note" not in reg.names()  # writing stays the librarian's alone


# -- catalog.py: the librarian's outcomes pass --------------------------------

def test_looks_failed_heuristic():
    assert catalog._looks_failed("ERROR: boom") is True
    assert catalog._looks_failed("DENIED by operator") is True
    assert catalog._looks_failed("exit code 1\nsome output") is True
    assert catalog._looks_failed("exit code 0\nok") is False
    assert catalog._looks_failed("wrote 5 chars") is False
    assert catalog._looks_failed("") is False


def test_maybe_reflect_outcomes_skips_when_nothing_failed(project, cfg, home):
    outcomes = [{"turn": 1, "tool": "write_file", "call": "x",
                 "expected": "should work", "actual": "wrote 5 chars"}]
    # backend=None would blow up if called — proves it never gets that far.
    assert catalog.maybe_reflect_outcomes(project, None, cfg, outcomes) is False


def test_maybe_reflect_outcomes_skips_when_no_outcomes(project, cfg, home):
    assert catalog.maybe_reflect_outcomes(project, None, cfg, []) is False


def test_reflect_outcomes_banks_a_hypothesis(project, cfg, home):
    outcomes = [{"turn": 2, "tool": "sandbox_shell", "call": "python m.py",
                 "expected": "prints 4", "actual": "exit code 1\nModuleNotFoundError: nope"}]
    backend = MockBackend([
        {"tool": "almanac_note",
         "args": {"topic": "missing-module", "claim": "m.py imports a module never installed",
                  "hypothesis": "the sandbox has no 'nope' package; pip install was skipped",
                  "expected": "prints 4", "actual": "ModuleNotFoundError: nope"}},
        {"text": "done"},
    ])
    assert catalog.maybe_reflect_outcomes(project, backend, cfg, outcomes) is True
    entry = almanac.get("missing-module")
    assert entry is not None
    assert "pip install was skipped" in entry["hypothesis"]
    assert entry["project"] == project.name


def test_reflect_outcomes_can_research_before_banking(project, cfg, home):
    outcomes = [{"turn": 1, "tool": "local_shell", "call": "curl example",
                 "expected": "200 OK", "actual": "ERROR: connection refused"}]
    backend = MockBackend([
        {"tool": "web_search", "args": {"query": "connection refused curl meaning"}},
        {"tool": "almanac_note",
         "args": {"topic": "conn-refused", "claim": "connection refused means nothing is listening",
                  "hypothesis": "researched it: the target port has no listener, not a network block",
                  "evidence": "web_search result"}},
    ])
    result = catalog.maybe_reflect_outcomes(project, backend, cfg, outcomes)
    assert result is True
    assert almanac.get("conn-refused") is not None


def test_reflect_outcomes_finish_run_is_rejected(project, cfg, home):
    outcomes = [{"turn": 1, "tool": "write_file", "call": "x",
                 "expected": "", "actual": "ERROR: disk full"}]
    backend = MockBackend([
        {"tool": "finish_run", "args": {"summary": "done"}},
        {"text": "oh, right — nothing to bank actually"},
    ])
    assert catalog.maybe_reflect_outcomes(project, backend, cfg, outcomes) is False


def test_outcomes_registry_is_narrow(cfg):
    names = catalog._outcomes_registry().names()
    assert "almanac_note" in names
    assert "web_search" in names
    assert "http_request" in names
    assert "local_shell" not in names
    assert "write_file" not in names
    assert "finish_run" not in names


# -- end-to-end through agent.run() -------------------------------------------

def test_almanac_off_records_no_outcomes_and_no_pass(project, cfg, home):
    cfg.set("almanac_enabled", False)
    result = run_agent(project, cfg, [
        {"tool": "sandbox_shell", "args": {"command": "false"}, "say": "expect exit 0"},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ])
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "outcome"' not in transcript
    assert '"role": "librarian"' not in transcript


def test_almanac_clean_run_records_outcomes_but_no_pass(project, cfg, home):
    cfg.set("almanac_enabled", True)
    result = run_agent(project, cfg, [
        {"tool": "write_file",
         "args": {"path": "workspace/out.txt", "content": "hi"},
         "say": "expect this to just write cleanly"},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ])
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "outcome"' in transcript  # ledger captured
    assert '"role": "librarian"' not in transcript  # nothing failed -> no pass
    assert almanac.index() == ""


def test_almanac_on_triggers_librarian_after_agent_run(project, cfg, home):
    cfg.set("almanac_enabled", True)
    cfg.set("skills_nudge", False)  # isolate the librarian; the sandbox ERROR
    # below would otherwise also trigger the skills nudge and consume the
    # scripted almanac_note meant for the librarian's own pass.
    result = run_agent(project, cfg, [
        {"tool": "sandbox_shell", "args": {"command": "python bad.py"},
         "say": "I expect this to print the total"},
        {"tool": "finish_run", "args": {"summary": "ran the script, it failed"}},
        # the librarian's own pass, next in the same scripted backend:
        {"tool": "almanac_note",
         "args": {"topic": "bad-py-crash", "claim": "bad.py crashes on a bad import",
                  "hypothesis": "the sandbox result showed a traceback pointing at a missing dep"}},
    ])
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "outcome"' in transcript
    assert '"role": "librarian"' in transcript
    assert almanac.get("bad-py-crash") is not None
