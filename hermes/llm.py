"""LLM backends behind one tiny interface.

OpenAIBackend talks to vLLM's OpenAI-compatible endpoint (through the SSH
tunnel, so base_url is localhost). MockBackend runs a scripted conversation
in-process — used by tests and `backend: mock` for GPU-free dry runs.

We speak the OpenAI chat-completions wire protocol over httpx directly rather
than pulling in the `openai` SDK: that package depends on `jiter`, a
Rust-built wheel with no prebuilt aarch64-linux-android distribution, so it
fails to install on Termux. httpx is already a dependency and the surface we
use (one POST to /chat/completions) is tiny.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from hermes.ui import dim, heartbeat, yellow


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, as the OpenAI API delivers it


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMTransportError(Exception):
    pass


class OpenAIBackend:
    RETRY_DELAYS = (1, 3, 8)

    def __init__(self, cfg, *, timeout=None, retry_delays=None, quiet=False):
        import httpx

        self._httpx = httpx
        self.cfg = cfg
        # quiet suppresses the heartbeat + retry chatter. The heartbeat is
        # proof-of-life for a call the OPERATOR is blocked on; a call made from
        # the background housekeeping thread (Phase 2) blocks no one — the
        # operator already has their prompt — so it must stay silent instead of
        # printing "waiting on the model" into the REPL under their cursor.
        self._quiet = quiet
        # retry_delays lets a bounded sibling (see .housekeeping()) turn off the
        # retry ladder entirely; None = the class default for real turns.
        if retry_delays is not None:
            self.RETRY_DELAYS = tuple(retry_delays)
        base_url = (cfg.get("base_url") or "").rstrip("/")
        self.url = f"{base_url}/chat/completions"
        eff_timeout = timeout if timeout is not None else cfg.get("llm_timeout", 300)
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {cfg.get('api_key', 'hermes')}"},
            timeout=float(eff_timeout or 300),
        )

    def housekeeping(self, *, quiet=False):
        """A bounded sibling for the librarian's side-passes — catalog
        enrichment, the almanac, retrospection, directive reconciliation, the
        skills nudge. Those run between the operator and their next prompt and
        are explicitly conveniences; they must NOT inherit a real turn's long
        `llm_timeout` or its retry ladder. Left to, a single slow completion
        on a loaded box blocks the REPL for the better part of an hour
        (llm_timeout × 4 attempts). Short timeout, single attempt: on a slow
        box the pass raises LLMTransportError, its caller no-ops, and the run's
        result — already fixed by the time these fire — simply stands.

        `quiet=True` also silences the heartbeat: the three passes that run in
        the background thread (Phase 2) block no one, so their "waiting on the
        model" ticks must not print into the operator's live prompt. The
        synchronous foreground passes (reconcile, skills nudge) keep quiet
        False — the operator IS waiting on those, so the proof-of-life stays."""
        t = self.cfg.get("housekeeping_timeout", 120) or 120
        return OpenAIBackend(self.cfg, timeout=float(t), retry_delays=(), quiet=quiet)

    def chat(self, messages, tools=None, tool_choice=None) -> ChatResult:
        sampling = self.cfg.get("sampling", {})
        body = dict(
            model=self.cfg.get("model"),
            messages=messages,
            temperature=sampling.get("temperature", 0.6),
            top_p=sampling.get("top_p", 0.95),
            max_tokens=self.cfg.get("max_completion_tokens", 8192),
            top_k=sampling.get("top_k", 20),
        )
        # Optional, per-model knobs — only sent when a model's build profile
        # sets them (min_p / penalties for the quantized + uncensored builds),
        # so the baseline request body stays exactly as it was.
        for knob in ("min_p", "presence_penalty", "frequency_penalty", "repetition_penalty"):
            if knob in sampling:
                body[knob] = sampling[knob]
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice

        hb_printer = (lambda *a, **k: None) if self._quiet else print
        last_error = None
        for attempt, delay in enumerate((0,) + self.RETRY_DELAYS, start=1):
            if delay and not self._quiet:
                print(yellow(
                    f"  vLLM/llama.cpp call failed ({last_error}) — "
                    f"retrying in {delay}s (attempt {attempt}/{1 + len(self.RETRY_DELAYS)})"
                ))
            if delay:
                time.sleep(delay)
            try:
                # A single completion can legitimately take minutes (a large
                # context to reprocess, a slow box) with nothing to show for
                # it until the whole response lands — the heartbeat is the
                # only thing standing between that and looking dead. Silenced
                # for a background (quiet) backend: no operator is blocked on it.
                with heartbeat("waiting on the model", printer=hb_printer):
                    resp = self.client.post(self.url, json=body)
                if resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    continue
                if resp.status_code >= 400:
                    # Client errors (bad request, context overflow, unsupported
                    # param, ...) won't fix themselves on retry — fail fast, and
                    # surface the body: it's the only place the real reason lives.
                    raise LLMTransportError(
                        f"{self.url} rejected the request: HTTP {resp.status_code}: "
                        f"{resp.text[:500]}"
                    )
                try:
                    msg = resp.json()["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    # A 2xx with a body that isn't the expected chat-completions
                    # shape (empty `choices`, non-JSON, ...) would otherwise crash
                    # the REPL with a raw traceback. Surface it as a clean
                    # transport error like every other backend failure.
                    raise LLMTransportError(
                        f"vLLM returned an unexpected response shape "
                        f"({type(e).__name__}). Body: {resp.text[:200]!r}"
                    ) from e
                calls = [
                    ToolCall(
                        tc["id"],
                        tc["function"]["name"],
                        tc["function"].get("arguments") or "{}",
                    )
                    for tc in (msg.get("tool_calls") or [])
                ]
                return ChatResult(content=msg.get("content"), tool_calls=calls)
            except self._httpx.TransportError as e:
                last_error = e
        raise LLMTransportError(
            f"vLLM unreachable at {self.cfg.get('base_url')} after retries "
            f"({last_error}). Check `gpu status` — the tunnel may be down."
        )


class MockBackend:
    """Scripted backend. Script items:
      {"text": "..."}                       -> plain assistant message
      {"tool": "name", "args": {...}}       -> single tool call
      {"tools": [{"tool":..., "args":...}]} -> several tool calls in one turn
    When the script runs dry: echoes, and obeys forced finish_run.
    """

    def __init__(self, script: list | None = None):
        self.script = list(script or [])
        self._counter = 0

    def housekeeping(self, *, quiet=False):
        # Nothing to bound or silence in-process; reuse the same script.
        return self

    def _tc(self, name: str, args: dict) -> ToolCall:
        self._counter += 1
        return ToolCall(f"mock-{self._counter}", name, json.dumps(args))

    def chat(self, messages, tools=None, tool_choice=None) -> ChatResult:
        if tool_choice and isinstance(tool_choice, dict):
            forced = tool_choice.get("function", {}).get("name")
            if forced == "finish_run":
                return ChatResult(
                    content=None,
                    tool_calls=[self._tc("finish_run", {"summary": "[mock] run done."})],
                )
        if self.script:
            item = self.script.pop(0)
            if "text" in item:
                return ChatResult(content=item["text"])
            if "tool" in item:
                return ChatResult(
                    content=item.get("say"),
                    tool_calls=[self._tc(item["tool"], item.get("args", {}))],
                )
            if "tools" in item:
                return ChatResult(
                    content=item.get("say"),
                    tool_calls=[
                        self._tc(t["tool"], t.get("args", {})) for t in item["tools"]
                    ],
                )
        tail = messages[-1]["content"] if messages else ""
        return ChatResult(content=f"[mock] I received: {str(tail)[-400:]}")


def make_backend(cfg):
    if cfg.get("backend") == "mock":
        return MockBackend()
    return OpenAIBackend(cfg)
