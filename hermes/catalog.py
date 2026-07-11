"""The librarian: a durable, self-describing catalog of what the agent has made.

The problem this closes: the workspace is a free-for-all output area. The
package only ever showed it as a bare listing (name + size), so a later run —
and the operator — could see *that* a file exists but never *what it is for* or
*whether it replaced something earlier*. Files piled up nameless; the same
script got re-derived three times and nobody noticed.

The fix is a small side-pass, run on the same every-N-runs cadence as
retrospection, that walks the workspace and keeps one index card per artifact:
what kind of thing it is, a one-line purpose, tags, provenance (which run made
it), and — inferred mechanically — what it supersedes or duplicates. The cards
then ride in the package in place of the bare listing, so the next run walks
into a room it recognizes.

Two layers, deliberately mirroring the rest of the system:

  - a DETERMINISTIC core (hashing, extension→kind, supersession/duplicate
    detection) that needs no model and never fails destructively, and
  - an optional LLM ENRICHMENT step that fills in the human-readable purpose and
    tags when a backend is available. If the model is down, mock, or returns
    junk, the deterministic cards stand on their own.

Storage is append-only JSONL (like history.jsonl): a rewrite of a path appends a
new card that *supersedes* the old one rather than deleting it, so provenance and
point-in-time answers survive. `current_entries()` collapses that log to the
live card per path; the superseded history is still on disk.

Scope: every card carries a `scope` field ("workspace" today) so a future
cross-workspace/shared lexicon is a flag flip, not a rewrite. The spanning
machinery is intentionally NOT built yet — there is one workspace to catalog.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from hermes.llm import LLMTransportError

# Extension → coarse kind, for the deterministic core. Unknown falls back to
# "file"; a NUL-sniff overrides everything to "binary".
_KIND_BY_EXT = {
    ".py": "script", ".sh": "script", ".bash": "script", ".rb": "script",
    ".js": "script", ".ts": "script", ".go": "script", ".rs": "script",
    ".md": "doc", ".rst": "doc", ".txt": "text",
    ".json": "data", ".jsonl": "data", ".csv": "data", ".tsv": "data",
    ".yaml": "config", ".yml": "config", ".toml": "config", ".ini": "config",
    ".html": "doc", ".pdf": "doc",
    ".png": "binary", ".jpg": "binary", ".jpeg": "binary", ".gif": "binary",
    ".zip": "binary", ".tar": "binary", ".gz": "binary", ".bin": "binary",
}

_SNIFF_BYTES = 1024


def _kind_for(rel: str, is_binary: bool) -> str:
    if is_binary:
        return "binary"
    return _KIND_BY_EXT.get(Path(rel).suffix.lower(), "file")


def _sniff_binary(path: Path) -> bool:
    """Cheap binary check: a NUL byte in the first KB, or bytes that won't decode
    as UTF-8. Good enough to decide 'don't sample this for the model'."""
    try:
        head = path.read_bytes()[:_SNIFF_BYTES]
    except OSError:
        return True
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


@dataclass
class Artifact:
    """One scanned workspace file, before it becomes a catalog card."""
    rel: str
    size: int
    mtime: float
    sha: str
    is_binary: bool
    large: bool  # over the byte cap: hashed by (size,mtime), not content


def scan_workspace(project, max_file_bytes: int = 200_000) -> list[Artifact]:
    """Every file under the workspace, with a content hash. Files over
    `max_file_bytes` are identity-hashed by (size, mtime) instead of content
    (marked `large`) so a huge generated blob never dominates the pass."""
    base = project.workspace_dir
    out: list[Artifact] = []
    if not base.is_dir():
        return out
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        rel = path.relative_to(base).as_posix()
        if st.st_size > max_file_bytes:
            sha = "large:" + hashlib.sha256(
                f"{st.st_size}:{st.st_mtime}".encode()
            ).hexdigest()[:16]
            out.append(Artifact(rel, st.st_size, st.st_mtime, sha,
                                is_binary=False, large=True))
            continue
        is_binary = _sniff_binary(path)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        out.append(Artifact(rel, st.st_size, st.st_mtime, digest[:16],
                            is_binary=is_binary, large=False))
    return out


def read_entries(project) -> list[dict]:
    """The full append-only card log, oldest first (superseded cards included)."""
    path = project.catalog_path
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


def current_entries(project) -> list[dict]:
    """The live card per path — the last card written for each path that has not
    since been deleted from the workspace. Sorted by path for a stable digest."""
    latest: dict[str, dict] = {}
    for e in read_entries(project):
        rel = e.get("path")
        if isinstance(rel, str):
            latest[rel] = e
    return sorted(latest.values(), key=lambda e: e.get("path", ""))


def _append_entries(project, entries: list[dict]) -> None:
    path = project.catalog_path
    with path.open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _new_and_changed(project, scanned: list[Artifact]) -> list[tuple[Artifact, dict | None]]:
    """Pair each scanned file with the prior live card for its path, keeping only
    files that are new or whose content hash changed. Unchanged files are
    dropped — the pass only ever works on what actually moved."""
    live = {e.get("path"): e for e in current_entries(project)}
    work: list[tuple[Artifact, dict | None]] = []
    for art in scanned:
        prior = live.get(art.rel)
        if prior is not None and prior.get("sha") == art.sha:
            continue  # unchanged since its card was written
        work.append((art, prior))
    return work


def _card(art: Artifact, prior: dict | None, scanned: list[Artifact],
          live_by_sha: dict[str, str], run_id, scope: str) -> dict:
    """Build one deterministic card. Purpose/tags start empty for enrichment."""
    short = art.sha.split(":")[-1][:10]
    card = {
        "id": f"{time.strftime('%Y%m%d-%H%M%S')}-{short}",
        "path": art.rel,
        "kind": _kind_for(art.rel, art.is_binary),
        "purpose": "",
        "tags": [],
        "sha": art.sha,
        "size": art.size,
        "large": art.large,
        "run": run_id,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "scope": scope,
    }
    # A rewrite of the same path supersedes the prior card for it.
    if prior is not None:
        card["supersedes"] = prior.get("id")
    # Identical content already living under a *different* path = a re-derivation.
    twin = live_by_sha.get(art.sha)
    if twin and twin != art.rel and not art.large:
        card["duplicate_of"] = twin
    return card


def _enrich(project, cards: list[dict], backend, cfg, think_re=None,
            log=None) -> None:
    """Best-effort: ask the model for {kind, purpose, tags} per card, mutating
    cards in place. Any failure (no backend, transport error, unparseable
    output) leaves the deterministic cards untouched — enrichment never blocks a
    pass and never raises."""
    if backend is None:
        return
    from hermes.agent import strip_think

    base = project.workspace_dir
    max_sample = int(cfg.get("catalog_sample_bytes", 1500))
    described = []
    for c in cards:
        if c.get("kind") == "binary" or c.get("large"):
            sample = "(binary or large file — not sampled)"
        else:
            try:
                sample = (base / c["path"]).read_text(errors="replace")[:max_sample]
            except OSError:
                sample = "(unreadable)"
        described.append({"path": c["path"], "sample": sample})

    listing = "\n\n".join(
        f"### {d['path']}\n{d['sample']}" for d in described
    )
    prompt = (
        "You are cataloguing files an agent wrote into its workspace. For EACH "
        "file below, return one JSON object with keys: path (exact, as given), "
        "kind (one short word: script/doc/data/config/report/note/other), "
        "purpose (one sentence, <=120 chars, what the file is FOR), and tags "
        "(1-4 short lowercase keywords). Return ONLY a JSON array, nothing "
        "else.\n\n" + listing
    )
    try:
        result = backend.chat([{"role": "user", "content": prompt}])
    except LLMTransportError:
        return
    text = strip_think(result.content, think_re) if think_re else strip_think(
        result.content
    )
    if log:
        log({"role": "catalog", "content": (text or "")[:2000]})
    parsed = _parse_json_array(text or "")
    if not parsed:
        return
    by_path = {c["path"]: c for c in cards}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        card = by_path.get(item.get("path"))
        if card is None:
            continue
        purpose = str(item.get("purpose", "")).strip()
        if purpose:
            card["purpose"] = purpose[:200]
        kind = str(item.get("kind", "")).strip().lower()
        if kind and card["kind"] in ("file", "text"):
            card["kind"] = kind[:20]  # trust the model only where we were unsure
        tags = item.get("tags")
        if isinstance(tags, list):
            card["tags"] = [str(t).strip().lower()[:24] for t in tags[:4] if str(t).strip()]


def _parse_json_array(text: str) -> list:
    """Pull the first JSON array out of a model reply, tolerating prose or code
    fences around it. Returns [] on anything unparseable."""
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def index(project, backend, cfg, run_id, think_re=None, log=None) -> int:
    """One catalog pass over the workspace. Returns the number of cards written
    (new or updated). Deterministic core always runs; LLM enrichment is
    best-effort on top. Never raises — a failed enrichment still banks the
    deterministic cards."""
    scanned = scan_workspace(project, int(cfg.get("catalog_max_file_bytes", 200_000)))
    work = _new_and_changed(project, scanned)
    if not work:
        return 0
    scope = str(cfg.get("catalog_scope", "workspace") or "workspace")
    # sha → path for the *current* live set, so duplicate detection sees siblings
    # that already have cards (not just files changed in this same pass).
    live_by_sha = {e.get("sha"): e.get("path") for e in current_entries(project)}
    for art in scanned:
        live_by_sha.setdefault(art.sha, art.rel)
    cards = [_card(art, prior, scanned, live_by_sha, run_id, scope)
             for art, prior in work]
    if cfg.get("catalog_enrich", True):
        _enrich(project, cards, backend, cfg, think_re=think_re, log=log)
    _append_entries(project, cards)
    return len(cards)


def digest(project, max_chars: int = 2000) -> str:
    """The always-present view for the package: one line per live card, richest
    first (cards with a purpose lead). Empty string when there is no catalog yet
    so the caller can fall back to the bare workspace listing."""
    entries = current_entries(project)
    if not entries:
        return ""
    entries.sort(key=lambda e: (e.get("purpose", "") == "", e.get("path", "")))
    lines = []
    for e in entries:
        tags = ",".join(e.get("tags", []))
        bits = [f"[{e.get('kind', 'file')}]", e.get("path", "?")]
        if e.get("purpose"):
            bits.append("— " + e["purpose"])
        if e.get("duplicate_of"):
            bits.append(f"(same content as {e['duplicate_of']})")
        if tags:
            bits.append(f"#{tags}")
        line = " ".join(bits)
        if sum(len(x) for x in lines) + len(line) > max_chars:
            lines.append(f"... ({len(entries) - len(lines)} more — ask for the catalog)")
            break
        lines.append(line)
    return "\n".join(lines)


def maybe_index(project, backend, cfg, run_id: int, think_re=None,
                log=None) -> int:
    """Trigger a pass when due — every `catalog_every_runs` runs (default 1, so
    the cheap deterministic core keeps the cards fresh each run). Gated by the
    caller on `catalog_enabled`. Returns cards written."""
    every = max(1, int(cfg.get("catalog_every_runs", 1)))
    if run_id % every != 0:
        return 0
    return index(project, backend, cfg, run_id, think_re=think_re, log=log)
