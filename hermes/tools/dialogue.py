"""Two-way dialogue with the operator while a run is in flight.

The agent can stop and ask its operator a real question, then wait for the
answer, over whichever live channel this run has:

- **Foreground `session`** — the operator is at the keyboard. `ask_operator_fn`
  reads their reply directly (blocking on stdin). This is the common case.
- **Detached `go`** — the operator is watching a log and answers from a
  separate process (`go`/`go say`), which appends to a JSONL inbox. The tool
  streams the question to that log and blocks on the inbox until a reply lands.

Either way the answer returns as the tool's result and flows into the
conversation like any other turn — a genuine back-and-forth, not silent
redirection. And either way it's bounded: the inbox wait can never exceed the
run's hard time budget (ctx.run_deadline), and if no answer comes the agent is
told to decide for itself. Use it for the decisions that genuinely turn on the
operator's intent, not routine steps.
"""

from __future__ import annotations

import time

from hermes import go_state
from hermes.tools.base import obj_schema, tool
from hermes.ui import dim, magenta

_POLL_SECONDS = 1.0

_REPLIED = (
    "Your operator replied (their direct answer to your question — weave it in "
    "and carry on):\n\n"
)
_UNANSWERED = (
    "No reply from your operator — they may have stepped away. Proceed on your "
    "best judgment now: decide, state the assumption you are making, and keep "
    "the work moving. You can surface the open question again in your summary."
)
_NO_CHANNEL = (
    "No live operator channel is open — this run isn't a live session, so there "
    "is no one to answer right now. Make the call yourself with your best "
    "judgment and state the assumption you made in your final summary."
)


@tool(
    "ask_operator",
    "Pause and ask your operator a question, then wait for their reply. Use "
    "this ONLY for genuinely influential forks: a decision that changes the "
    "direction of the work, a fact only they have, a trade-off they should own. "
    "Do NOT use it for routine steps or to ask permission for actions the "
    "safety gates already cover. Your question is shown to them live and their "
    "reply comes back as this tool's result. If no one answers in time you will "
    "be told to proceed on your own judgment — so ask real questions, and keep "
    "the work moving while you can.",
    obj_schema({"question": {"type": "string"}}, ["question"]),
)
def ask_operator(args, ctx):
    question = str(args.get("question", "")).strip()
    if not question:
        return "ERROR: ask_operator needs a non-empty 'question'."

    # Show the question prominently in the live view, whatever the channel.
    print(magenta("\n  ◆ Hermes is asking you:"))
    print(magenta("    " + question))

    ask_fn = getattr(ctx, "ask_operator_fn", None)
    if ask_fn is not None:
        # Foreground session: the operator is right here at the keyboard.
        print(dim("    (type your answer, or just Enter to let it decide)\n"))
        try:
            reply = ask_fn(question)
        except Exception:
            reply = ""
        if reply and reply.strip():
            return _REPLIED + reply.strip()
        return _UNANSWERED

    if ctx.inbox_path:
        return _wait_on_inbox(ctx, question)

    print(dim("    (no live channel — deciding without them)\n"))
    return _NO_CHANNEL


def _wait_on_inbox(ctx, question: str) -> str:
    """Detached `go`: block on the inbox a separate `go`/`go say` writes to,
    bounded by ask_operator_timeout and the run's hard deadline."""
    try:
        timeout = float(ctx.cfg.get("ask_operator_timeout", 900))
    except (TypeError, ValueError):
        timeout = 900.0
    deadline = time.monotonic() + timeout
    if ctx.run_deadline is not None:
        # Leave the loop a few seconds to still wrap up within its hard budget.
        deadline = min(deadline, ctx.run_deadline - 5)

    wait_s = max(0, int(deadline - time.monotonic()))
    wait_label = f"~{wait_s // 60} min" if wait_s >= 60 else f"~{wait_s}s"
    print(dim(f"    (reply with `go <your answer>` — waiting up to {wait_label})\n"))

    while time.monotonic() < deadline:
        replies = go_state.drain_inbox(ctx.inbox_path)
        if replies:
            print(dim("  ◆ operator replied — continuing.\n"))
            return _REPLIED + "\n\n".join(replies)
        time.sleep(_POLL_SECONDS)
    return _UNANSWERED


TOOLS = [ask_operator]
