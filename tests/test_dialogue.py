import json
import threading
import time

from hermes import go_state
from hermes.tools.base import ToolContext
from hermes.tools.dialogue import ask_operator as _ask_operator_tool

ask_operator = _ask_operator_tool.fn


# ---------------------------------------------------------------- drain_inbox
def test_drain_inbox_reads_and_consumes(tmp_path):
    inbox = tmp_path / "s.inbox.jsonl"
    inbox.write_text(
        json.dumps({"text": "first"}) + "\n" + json.dumps({"text": "second"}) + "\n"
    )
    assert go_state.drain_inbox(inbox) == ["first", "second"]
    assert not inbox.exists()  # consumed atomically
    assert go_state.drain_inbox(inbox) == []  # nothing left


def test_drain_inbox_missing_file_is_empty(tmp_path):
    assert go_state.drain_inbox(tmp_path / "nope.jsonl") == []


def test_drain_inbox_skips_garbage_and_blank(tmp_path):
    inbox = tmp_path / "s.inbox.jsonl"
    inbox.write_text("not json\n" + json.dumps({"text": "   "}) + "\n"
                     + json.dumps({"text": "real"}) + "\n")
    assert go_state.drain_inbox(inbox) == ["real"]


# ---------------------------------------------------------------- ask_operator
def _ctx(cfg, inbox_path=None, run_deadline=None):
    return ToolContext(project=None, cfg=cfg, inbox_path=inbox_path,
                       run_deadline=run_deadline)


def test_ask_operator_returns_the_reply(cfg, tmp_path):
    inbox = tmp_path / "s.inbox.jsonl"
    inbox.write_text(json.dumps({"text": "go with option B"}) + "\n")
    cfg.set("ask_operator_timeout", 5)
    out = ask_operator({"question": "A or B?"}, _ctx(cfg, inbox_path=inbox))
    assert "go with option B" in out
    assert not inbox.exists()  # the reply was consumed, not left for the next turn


def test_ask_operator_without_channel_falls_back(cfg):
    out = ask_operator({"question": "anything?"}, _ctx(cfg, inbox_path=None))
    assert "No live operator channel" in out


def test_ask_operator_times_out_gracefully(cfg, tmp_path):
    inbox = tmp_path / "s.inbox.jsonl"  # never created -> no reply
    cfg.set("ask_operator_timeout", 0)  # don't actually wait
    out = ask_operator({"question": "still there?"}, _ctx(cfg, inbox_path=inbox))
    assert "No reply" in out


def test_ask_operator_respects_run_deadline(cfg, tmp_path):
    inbox = tmp_path / "s.inbox.jsonl"
    cfg.set("ask_operator_timeout", 9999)  # would wait forever...
    # ...but the run's hard deadline has already passed, so it returns at once.
    start = time.monotonic()
    out = ask_operator({"question": "?"}, _ctx(cfg, inbox_path=inbox,
                                               run_deadline=time.monotonic() - 1))
    assert "No reply" in out
    assert time.monotonic() - start < 1.0  # did not block


def test_ask_operator_empty_question_errors(cfg, tmp_path):
    out = ask_operator({"question": "   "}, _ctx(cfg, inbox_path=tmp_path / "x"))
    assert out.startswith("ERROR")


def test_ask_operator_picks_up_a_reply_that_arrives_mid_wait(cfg, tmp_path, monkeypatch):
    import hermes.tools.dialogue as dialogue
    monkeypatch.setattr(dialogue, "_POLL_SECONDS", 0.02)
    inbox = tmp_path / "s.inbox.jsonl"
    cfg.set("ask_operator_timeout", 5)

    def answer_later():
        time.sleep(0.1)
        inbox.write_text(json.dumps({"text": "yes, ship it"}) + "\n")

    t = threading.Thread(target=answer_later)
    t.start()
    out = ask_operator({"question": "ship?"}, _ctx(cfg, inbox_path=inbox))
    t.join()
    assert "yes, ship it" in out
