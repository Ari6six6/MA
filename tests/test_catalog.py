"""The librarian (hermes/catalog.py): a self-describing card per workspace
artifact. Deterministic core (scan/hash/kind/supersession/duplicate) plus a
best-effort LLM enrichment that fills purpose/tags without ever blocking a pass.
"""

import json

from hermes import agent, catalog
from hermes.llm import LLMTransportError, MockBackend


def write_ws(project, rel, content):
    """Write a file under the project workspace, creating parents."""
    path = project.workspace_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


# -- scan --------------------------------------------------------------------

def test_scan_hashes_and_classifies(project):
    write_ws(project, "a.py", "print('hi')")
    write_ws(project, "sub/b.txt", "notes")
    arts = {a.rel: a for a in catalog.scan_workspace(project)}
    assert set(arts) == {"a.py", "sub/b.txt"}
    assert arts["a.py"].sha and not arts["a.py"].is_binary
    assert arts["a.py"].size == len("print('hi')")


def test_scan_flags_binary(project):
    write_ws(project, "img.dat", b"\x00\x01\x02binary\x00")
    art = catalog.scan_workspace(project)[0]
    assert art.is_binary is True


def test_scan_large_files_identity_hashed(project):
    write_ws(project, "big.data", "x" * 500)
    art = catalog.scan_workspace(project, max_file_bytes=100)[0]
    assert art.large is True
    assert art.sha.startswith("large:")


# -- deterministic core ------------------------------------------------------

def test_index_writes_cards_without_a_backend(project, cfg):
    write_ws(project, "scrape.py", "print(1)")
    write_ws(project, "readme.md", "# hi")
    n = catalog.index(project, None, cfg, run_id=1)
    assert n == 2
    cards = {c["path"]: c for c in catalog.current_entries(project)}
    assert cards["scrape.py"]["kind"] == "script"
    assert cards["readme.md"]["kind"] == "doc"
    assert cards["scrape.py"]["run"] == 1
    assert cards["scrape.py"]["scope"] == "workspace"
    # no backend -> no purpose enrichment, deterministic cards stand
    assert cards["scrape.py"]["purpose"] == ""


def test_unchanged_file_is_not_recatalogued(project, cfg):
    write_ws(project, "x.py", "v1")
    assert catalog.index(project, None, cfg, run_id=1) == 1
    # second pass, nothing changed -> zero new cards
    assert catalog.index(project, None, cfg, run_id=2) == 0
    assert len(catalog.read_entries(project)) == 1


def test_rewrite_supersedes_prior_card(project, cfg):
    write_ws(project, "x.py", "v1")
    catalog.index(project, None, cfg, run_id=1)
    first = catalog.current_entries(project)[0]
    write_ws(project, "x.py", "v2 changed")
    assert catalog.index(project, None, cfg, run_id=2) == 1
    log = catalog.read_entries(project)
    assert len(log) == 2  # append-only: the old card is retained
    live = catalog.current_entries(project)
    assert len(live) == 1  # but only one live card per path
    assert live[0]["supersedes"] == first["id"]
    assert live[0]["run"] == 2


def test_duplicate_content_under_new_path_is_flagged(project, cfg):
    write_ws(project, "orig.py", "identical body")
    write_ws(project, "copy.py", "identical body")
    catalog.index(project, None, cfg, run_id=1)
    cards = {c["path"]: c for c in catalog.current_entries(project)}
    # exactly one of them points at the other as the same content
    dups = [p for p, c in cards.items() if c.get("duplicate_of")]
    assert len(dups) == 1
    dup = cards[dups[0]]
    assert dup["duplicate_of"] in ("orig.py", "copy.py")
    assert dup["duplicate_of"] != dups[0]


# -- enrichment --------------------------------------------------------------

def test_enrichment_fills_purpose_and_tags(project, cfg):
    write_ws(project, "notes.txt", "some notes here")
    reply = json.dumps([
        {"path": "notes.txt", "kind": "note",
         "purpose": "Running notes for the scrape task", "tags": ["notes", "scrape"]},
    ])
    backend = MockBackend([{"text": reply}])
    catalog.index(project, backend, cfg, run_id=1)
    card = catalog.current_entries(project)[0]
    assert card["purpose"] == "Running notes for the scrape task"
    assert card["tags"] == ["notes", "scrape"]
    # deterministic kind was "text"; model was trusted to sharpen it
    assert card["kind"] == "note"


def test_enrichment_does_not_override_confident_kind(project, cfg):
    write_ws(project, "run.py", "print(1)")
    reply = json.dumps([
        {"path": "run.py", "kind": "doc", "purpose": "p", "tags": []},
    ])
    backend = MockBackend([{"text": reply}])
    catalog.index(project, backend, cfg, run_id=1)
    card = catalog.current_entries(project)[0]
    # extension said "script" (confident) -> model's "doc" is ignored for kind
    assert card["kind"] == "script"
    assert card["purpose"] == "p"


def test_enrichment_junk_leaves_deterministic_cards(project, cfg):
    write_ws(project, "a.py", "print(1)")
    backend = MockBackend([{"text": "sorry, I can't do that"}])
    n = catalog.index(project, backend, cfg, run_id=1)
    assert n == 1
    card = catalog.current_entries(project)[0]
    assert card["kind"] == "script"
    assert card["purpose"] == ""  # junk reply -> no enrichment, card still banked


def test_enrichment_transport_error_is_noop_but_cards_land(project, cfg):
    write_ws(project, "a.py", "print(1)")

    class DeadBackend:
        def chat(self, *a, **k):
            raise LLMTransportError("down")

    n = catalog.index(project, DeadBackend(), cfg, run_id=1)
    assert n == 1  # deterministic card still written
    assert catalog.current_entries(project)[0]["purpose"] == ""


def test_enrich_disabled_skips_the_model(project, cfg):
    cfg.set("catalog_enrich", False)
    write_ws(project, "a.py", "print(1)")

    class Boom:
        def chat(self, *a, **k):
            raise AssertionError("enrichment ran despite catalog_enrich=False")

    assert catalog.index(project, Boom(), cfg, run_id=1) == 1


# -- digest ------------------------------------------------------------------

def test_digest_empty_before_any_pass(project):
    assert catalog.digest(project) == ""


def test_digest_lists_cards_with_purpose_first(project, cfg):
    write_ws(project, "plain.py", "print(1)")
    write_ws(project, "described.py", "print(2)")
    reply = json.dumps([
        {"path": "described.py", "kind": "script",
         "purpose": "Entry point", "tags": ["main"]},
    ])
    catalog.index(project, MockBackend([{"text": reply}]), cfg, run_id=1)
    view = catalog.digest(project)
    assert "described.py" in view and "Entry point" in view
    assert "plain.py" in view
    # the described card (has a purpose) sorts ahead of the bare one
    assert view.index("described.py") < view.index("plain.py")


# -- trigger -----------------------------------------------------------------

def test_maybe_index_respects_cadence(project, cfg):
    write_ws(project, "a.py", "print(1)")
    cfg.set("catalog_every_runs", 3)
    assert catalog.maybe_index(project, None, cfg, run_id=2) == 0  # not due
    assert catalog.read_entries(project) == []
    assert catalog.maybe_index(project, None, cfg, run_id=3) == 1  # due


# -- end to end through a run -----------------------------------------------

def test_agent_run_catalogues_written_files(project, cfg):
    cfg.set("catalog_enrich", False)  # keep the run's backend script clean
    backend = MockBackend([
        {"tool": "write_file",
         "args": {"path": "workspace/out.py", "content": "print('done')"}},
        {"tool": "finish_run", "args": {"summary": "Did: wrote out.py"}},
    ])
    agent.run(project, "make out.py", cfg, backend, env={},
              confirm_fn=lambda *a, **k: True)
    cards = {c["path"]: c for c in catalog.current_entries(project)}
    assert "out.py" in cards
    assert cards["out.py"]["kind"] == "script"


def test_catalog_off_by_config_writes_nothing(project, cfg):
    cfg.set("catalog_enabled", False)
    backend = MockBackend([
        {"tool": "write_file",
         "args": {"path": "workspace/out.py", "content": "x=1"}},
        {"tool": "finish_run", "args": {"summary": "Did: a"}},
    ])
    agent.run(project, "go", cfg, backend, env={},
              confirm_fn=lambda *a, **k: True)
    assert catalog.read_entries(project) == []
