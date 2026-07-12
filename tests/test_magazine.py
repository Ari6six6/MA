"""The librarian's magazine: the forward brief (morning) and the debate
attempt-register (night) — the other half of the almanac, aimed at debate mode
where a prose turn never trips the outcome-failure gate."""

from hermes import agent, almanac, magazine, package
from hermes.llm import MockBackend


# -- strategy.md: the campaign plan the librarian checks against ---------------

def test_strategy_absent_by_default(project, cfg, home):
    assert project.read_strategy() == ""
    user = package.assemble(project, "x", {}, cfg)[1]["content"]
    assert "# STRATEGY" not in user


def test_strategy_rides_in_package_when_set(project, cfg, home):
    project.write_strategy("# Strategy\n\nShip the narrow slice first.")
    user = package.assemble(project, "x", {}, cfg)[1]["content"]
    assert "# STRATEGY" in user
    assert "Ship the narrow slice first." in user
    # After the mission, before the request.
    assert user.index("# MISSION") < user.index("# STRATEGY") < user.index("# CURRENT REQUEST")


def test_librarian_sets_the_strategy_during_compose(project, cfg, home):
    # The strategy is the librarian's: an empty one is set from the compose pass,
    # not authored by the operator.
    assert project.read_strategy() == ""
    backend = MockBackend([
        {"tool": "write_strategy",
         "args": {"text": "# Strategy\n\nWin by shipping the narrowest useful slice."}},
        {"tool": "write_magazine",
         "args": {"text": "BRIEF: set the line — narrow slices, no rewrites."}},
        {"text": "done"},
    ])
    text = magazine.compose(project, backend, cfg, "what's the plan?")
    assert text is not None
    assert "narrowest useful slice" in project.read_strategy()


# -- the magazine file --------------------------------------------------------

def test_write_and_read_magazine_roundtrip(project, home):
    magazine.write_magazine(project, "MORNING: you already tried X.")
    assert "MORNING: you already tried X." in magazine.read_magazine(project)
    assert magazine.magazine_path(project).exists()


def test_read_magazine_empty_when_absent(project, home):
    assert magazine.read_magazine(project) == ""


# -- narrow registries --------------------------------------------------------

def test_compose_registry_is_narrow():
    names = magazine._compose_registry().names()
    assert "write_magazine" in names
    assert "write_strategy" in names        # the librarian keeps the strategy
    assert "web_search" in names and "http_request" in names
    assert "load_almanac" in names          # may read a card before citing it
    assert "almanac_note" not in names      # the morning brief never banks
    assert "write_file" not in names and "finish_run" not in names


def test_register_registry_is_narrow():
    names = magazine._register_registry().names()
    assert "almanac_note" in names          # the night pass banks the line
    assert "load_almanac" in names and "web_search" in names
    assert "write_magazine" not in names    # the night pass never writes the brief
    assert "write_file" not in names and "finish_run" not in names


# -- compose(): the morning pass ----------------------------------------------

def test_compose_writes_magazine_and_returns_text(project, cfg, home):
    backend = MockBackend([
        {"tool": "write_magazine",
         "args": {"text": "BRIEF: you argued approach X on run 1 — it stalled."}},
        {"text": "done, magazine left on the desk"},
    ])
    text = magazine.compose(project, backend, cfg, "should we do X?")
    assert text is not None
    assert "you argued approach X on run 1" in text
    assert "you argued approach X on run 1" in magazine.read_magazine(project)


def test_compose_returns_none_when_nothing_written(project, cfg, home):
    # A pass that only talks and never calls write_magazine leaves no brief.
    backend = MockBackend([{"text": "nothing worth saying this morning"}])
    assert magazine.compose(project, backend, cfg, "anything?") is None


def test_compose_can_research_before_writing(project, cfg, home):
    backend = MockBackend([
        {"tool": "web_search", "args": {"query": "does approach X scale"}},
        {"tool": "write_magazine",
         "args": {"text": "BRIEF: researched X — it doesn't scale past N. Hold off."}},
        {"text": "done"},
    ])
    text = magazine.compose(project, backend, cfg, "push on X?")
    assert text is not None and "doesn't scale" in text


# -- register_attempt(): the night pass ---------------------------------------

def test_register_attempt_banks_the_line(project, cfg, home):
    backend = MockBackend([
        {"tool": "almanac_note",
         "args": {"topic": "approach-x", "claim": "agent argued approach X again",
                  "hypothesis": "a repeat of run 1's line; the strategy says avoid X"}},
        {"text": "logged the line"},
    ])
    ok = magazine.register_attempt(
        project, backend, cfg, "should we do X?", "I think we should do X."
    )
    assert ok is True
    entry = almanac.get("approach-x")
    assert entry is not None and "repeat of run 1" in entry["hypothesis"]


def test_register_attempt_noop_on_empty_reply(project, cfg, home):
    # backend=None would blow up if reached — proves the empty-reply short-circuit.
    assert magazine.register_attempt(project, None, cfg, "prompt", "") is False


# -- package injection: magazine stands in for the memo -----------------------

def test_package_injects_magazine_before_request(project, cfg, home):
    user = package.assemble(
        project, "go", {}, cfg, magazine_text="BRIEF: watch the loop on X."
    )[1]["content"]
    assert "# LIBRARIAN'S MAGAZINE" in user
    assert "watch the loop on X." in user
    assert user.index("# LIBRARIAN'S MAGAZINE") < user.index("# CURRENT REQUEST")


def test_magazine_replaces_the_new_since_memo(project, cfg, home):
    cfg.set("almanac_enabled", True)
    almanac.write_entry("bad-x", "X crashes", "missing dep")  # would surface as a memo
    # With a magazine in hand, the raw new-since memo is stood down for it.
    user = package.assemble(
        project, "go", {}, cfg, magazine_text="BRIEF: the librarian already read that card."
    )[1]["content"]
    assert "# LIBRARIAN'S MAGAZINE" in user
    assert "# LIBRARIAN MEMO" not in user


def test_magazine_truncated_to_budget(project, cfg, home):
    cfg.set("magazine_chars", 200)
    user = package.assemble(
        project, "go", {}, cfg, magazine_text="B " + "x" * 5000
    )[1]["content"]
    assert "[...truncated...]" in user


def test_no_magazine_keeps_memo_behavior(project, cfg, home):
    cfg.set("almanac_enabled", True)
    almanac.write_entry("bad-x", "X crashes", "missing dep")
    user = package.assemble(project, "go", {}, cfg)[1]["content"]  # no magazine_text
    assert "# LIBRARIAN MEMO" in user
    assert "# LIBRARIAN'S MAGAZINE" not in user


# -- end to end through agent.run(mode="debate") ------------------------------

def test_debate_mode_composes_ahead_and_registers_behind(project, cfg, home):
    cfg.set("magazine_enabled", True)
    # Isolate the magazine passes from the other end-of-run passes so the one
    # scripted backend is consumed in a predictable order.
    cfg.set("catalog_enabled", False)
    cfg.set("almanac_enabled", False)
    cfg.set("skills_nudge", False)
    backend = MockBackend([
        # 1) morning compose (runs before the package is assembled)
        {"tool": "write_magazine",
         "args": {"text": "BRIEF: you already argued X on an earlier turn."}},
        {"text": "desk is ready"},
        # 2) the debate turn itself — prose plus a finish
        {"tool": "finish_run", "args": {"summary": "argued for X"},
         "say": "I think we should commit to X."},
        # 3) the night register pass
        {"tool": "almanac_note",
         "args": {"topic": "approach-x", "claim": "argued approach X",
                  "hypothesis": "a repeat; strategy says hold off on X"}},
        {"text": "logged"},
    ])
    result = agent.run(
        project, "should we do X?", cfg, backend,
        env={}, confirm_fn=lambda *a, **k: True, mode="debate",
    )
    assert not result.aborted
    # Ahead in the morning: the brief was written.
    assert "you already argued X" in magazine.read_magazine(project)
    # Behind at night: the line was registered to the almanac.
    assert almanac.get("approach-x") is not None
