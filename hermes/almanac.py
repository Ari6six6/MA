"""The almanac: cross-project hypotheses about why things worked or didn't.

Distinct from the two stores that already exist:
  - skills (hermes/skills.py) are procedures — HOW to do something.
  - the catalog (hermes/catalog.py) is per-project artifact cards — WHAT a
    file is. Its own docstring names the gap this closes: "a future
    cross-workspace/shared lexicon is a flag flip, not a rewrite."

The almanac is neither: it's a single GLOBAL, cross-project log of hypotheses
— WHY a specific expected outcome didn't match what actually happened (or
did). One card per topic, written by the end-of-run librarian pass
(hermes/librarian.py), read by every run of every project the same way a
skill is: an index of one-liners in the system prompt, the full writeup on
demand via `load_almanac`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from hermes.config import hermes_home

TOPIC_RE = re.compile(r"^[a-z0-9_-]{1,60}$")


def almanac_path() -> Path:
    return hermes_home() / "almanac.jsonl"


def read_entries() -> list[dict]:
    """The full append-only card log, oldest first (superseded cards included)."""
    path = almanac_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def current_entries() -> list[dict]:
    """The live card per topic — a later run can refine or correct an earlier
    hypothesis; the newer one supersedes without erasing the trail."""
    latest: dict[str, dict] = {}
    for e in read_entries():
        topic = e.get("topic")
        if isinstance(topic, str):
            latest[topic] = e
    return sorted(latest.values(), key=lambda e: e.get("topic", ""))


def get(topic: str) -> dict | None:
    topic = str(topic).strip().lower().replace(" ", "-")
    return {e.get("topic"): e for e in current_entries()}.get(topic)


def write_entry(topic: str, claim: str, hypothesis: str, *, expected: str = "",
                 actual: str = "", evidence: str = "", confidence: str = "",
                 project: str = "", run: int = 0) -> str:
    """Append (or refine) one hypothesis card. Refusing an empty claim/hypothesis
    keeps this from becoming a junk drawer of 'X failed' with no theory attached
    — the whole point is the WHY, not just a record that something happened."""
    topic = str(topic).strip().lower().replace(" ", "-")
    if not TOPIC_RE.match(topic):
        return ("ERROR: topic must be a short slug matching [a-z0-9_-]{1,60} "
                "(e.g. 'vast-ssh-timeout'), lowercase letters/digits/hyphens only.")
    claim = str(claim).strip()
    if not claim:
        return "ERROR: claim is required — the one-line lesson this entry teaches."
    hypothesis = str(hypothesis).strip()
    if not hypothesis:
        return "ERROR: hypothesis is required — your theory of WHY, not just what happened."
    prior = get(topic)
    card = {
        "id": f"{time.strftime('%Y%m%d-%H%M%S')}-{topic}",
        "topic": topic,
        "claim": claim[:200],
        "hypothesis": hypothesis[:2000],
        "expected": str(expected).strip()[:500],
        "actual": str(actual).strip()[:500],
        "evidence": str(evidence).strip()[:1000],
        "confidence": str(confidence).strip().lower()[:20],
        "project": str(project),
        "run": run,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    if prior is not None:
        card["supersedes"] = prior.get("id")
    path = almanac_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(card) + "\n")
    verb = "refined" if prior is not None else "banked"
    return f"{verb} almanac entry '{topic}'."


def index(max_chars: int = 1200) -> str:
    """The one-liner menu for the system prompt — every project, one shared
    list, same budget-capped shape as the skills index and the catalog digest."""
    entries = current_entries()
    if not entries:
        return ""
    lines = []
    for e in entries:
        conf = f" ({e['confidence']})" if e.get("confidence") else ""
        line = f"- `{e.get('topic')}`{conf} — {e.get('claim', '')}"
        if sum(len(x) for x in lines) + len(line) > max_chars:
            lines.append(f"... ({len(entries) - len(lines)} more — `load_almanac` by topic)")
            break
        lines.append(line)
    return "\n".join(lines)


def latest_id() -> str | None:
    """The id of the most recently written card, or None if the almanac is
    empty. IDs are `<timestamp>-<topic>`, so lexical order is chronological."""
    entries = read_entries()
    return max((e["id"] for e in entries if e.get("id")), default=None)


def new_since(cursor: str | None, max_chars: int = 1500) -> str:
    """The librarian's memo: current cards touched (banked or refined) after
    `cursor`, an id from a project's own last run (see Project.almanac_cursor).
    Unlike `index`, this is meant to be READ, not just scanned for a topic to
    look up — so it carries the claim AND the hypothesis, not just the claim.
    Empty when nothing changed since `cursor`, so a quiet stretch adds no
    section at all rather than an empty header."""
    new = [e for e in current_entries() if cursor is None or e.get("id", "") > cursor]
    if not new:
        return ""
    new.sort(key=lambda e: e.get("id", ""))
    lines = []
    total = 0
    for e in new:
        conf = f" ({e['confidence']})" if e.get("confidence") else ""
        block = (f"- `{e.get('topic')}`{conf} — {e.get('claim', '')}\n"
                 f"  why: {e.get('hypothesis', '')}")
        if total + len(block) > max_chars:
            lines.append(f"... ({len(new) - len(lines)} more new — `load_almanac` by topic)")
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines)
