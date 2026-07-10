"""Live two-way dialogue with the operator while a run is in flight.

`go` runs the agent in a detached background process the operator watches with
`go attach` and talks to with `go`/`go say` (which append to a per-space JSONL
inbox). That channel was one-way — the operator could steer, but the agent
could never stop and ASK. `ask_operator` closes the loop: the agent poses a
real question, the question streams to the live log the operator is watching,
and the call blocks on that same inbox until a reply lands (or a bounded wait
elapses). The answer comes back as the tool's result, so it flows into the
conversation like any other turn — a genuine back-and-forth, not silent
redirection.

Bounded on purpose: the wait never exceeds the run's hard time budget
(ctx.run_deadline) and defaults to `ask_operator_timeout` seconds, so an
unattended run whose operator has wandered off proceeds on its own judgment
instead of hanging until the run's cap kills it.
"""

from __future__ import annotations

import time

from hermes import go_state
from hermes.tools.base import obj_schema, tool
from hermes.ui import dim, magenta

_POLL_SECONDS = 1.0


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
    if not ctx.inbox_path:
        return (
            "No live operator channel is open — this run isn't attached to a "
            "`go` session, so there is no one to answer right now. Make the call "
            "yourself with your best judgment and state the assumption you made "
            "in your final summary."
        )

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
    # Stream the question to the live log the operator is watching (in `go` mode
    # stdout is the redirected log that `go attach` tails), with a clear cue for
    # how to answer.
    print(magenta("\n  ◆ Hermes is asking you:"))
    print(magenta("    " + question))
    print(dim(f"    (reply with `go <your answer>` — waiting up to {wait_label})\n"))

    while time.monotonic() < deadline:
        replies = go_state.drain_inbox(ctx.inbox_path)
        if replies:
            print(dim("  ◆ operator replied — continuing.\n"))
            return (
                "Your operator replied (their direct answer to your question — "
                "weave it in and carry on):\n\n" + "\n\n".join(replies)
            )
        time.sleep(_POLL_SECONDS)

    return (
        "No reply from your operator within the wait window — they may have "
        "stepped away from the live view. Proceed on your best judgment now: "
        "decide, state the assumption you are making, and keep the work moving. "
        "You can surface the open question again in your final summary."
    )


TOOLS = [ask_operator]
