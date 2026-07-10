"""The Village: embodied delegation on a shared, air-gapped Docker network.

These assert on the docker *command strings* (the real contract, like
test_sandbox_exec) via a command-matching fake daemon — no real Docker needed.
The security-load-bearing checks: citizens join an `--internal` network (siblings
reachable, no route out), the air-gap default is untouched when the village is off,
and a body is always harvested (logs + inspect) BEFORE it is removed.
"""

import json

from hermes.sandbox import village


class DockerFakeEp:
    """A fake daemon that answers by command shape and records every call.
    `existing_networks` controls what `network ls` reports as already present."""

    def __init__(self, existing_networks=(), exec_out="ran in body"):
        self.calls: list[str] = []
        self.existing_networks = set(existing_networks)
        self.exec_out = exec_out

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        c = command
        if "command -v docker" in c:
            return (0, "docker", "")
        if "network ls" in c:
            hit = next((n for n in self.existing_networks if f"^{n}$" in c), "")
            return (0, hit, "")
        if "network create" in c:
            return (0, "netid", "")
        if "network rm" in c:
            return (0, "", "")
        if c.strip().startswith("docker ps") or " ps -a" in c:
            return (0, "", "")  # nothing matches -> caller will create
        if " run -d " in c:
            return (0, "containerid", "")
        if " logs " in c:
            return (0, "citizen stdout+stderr", "")
        if " inspect " in c:
            return (0, '[{"Name":"x","State":{"ExitCode":0}}]', "")
        if " exec -w " in c:
            return (0, self.exec_out, "")
        if " rm -f " in c or c.strip().startswith("docker rm"):
            return (0, "", "")
        if "docker ps" in c:
            return (0, "", "")
        return (0, "", "")


class Cfg:
    def __init__(self, **over):
        self.d = {"village_network": "hermes-net", "village_internal": True,
                  "village_image": "", "sandbox_image": "python:3.12-slim",
                  "village_max_citizens": 8}
        self.d.update(over)

    def get(self, k, default=None):
        return self.d.get(k, default)


# ---- naming ------------------------------------------------------------------

def test_citizen_name_is_dns_safe(project):
    # Uppercase, spaces, and dots collapse to a valid lowercase DNS label.
    n = village.citizen_name(project, role="Scraper Bot.v2", gen=2, n=3)
    assert n == n.lower()
    assert all(ch.isalnum() or ch == "-" for ch in n)
    assert not n.startswith("-") and not n.endswith("-")
    assert len(n) < 63


# ---- network -----------------------------------------------------------------

def test_ensure_network_creates_internal_bridge():
    ep = DockerFakeEp()
    name = village.ensure_network(ep, Cfg(), runtime="docker")
    assert name == "hermes-net"
    created = [c for c in ep.calls if "network create" in c]
    assert len(created) == 1
    assert "--internal" in created[0]
    assert "--label hermes.village=1" in created[0]
    assert "hermes-net" in created[0]


def test_ensure_network_is_idempotent():
    ep = DockerFakeEp(existing_networks=["hermes-net"])
    village.ensure_network(ep, Cfg(), runtime="docker")
    assert not any("network create" in c for c in ep.calls)  # already there


def test_internal_flag_dropped_when_configured_open():
    ep = DockerFakeEp()
    village.ensure_network(ep, Cfg(village_internal=False), runtime="docker")
    created = [c for c in ep.calls if "network create" in c][0]
    assert "--internal" not in created


# ---- citizens ----------------------------------------------------------------

def test_ensure_citizen_run_cmd_has_labels_network_and_ro_dna(project):
    ep = DockerFakeEp()
    village.ensure_citizen(
        ep, project, "hermes-p-scraper-g1-01", Cfg(),
        parent="main", generation=1, role="scraper", run_id="0007",
        runtime="docker", dna_dir="/proj/pop/x/dna",
    )
    run_cmd = [c for c in ep.calls if " run -d " in c][-1]
    assert "--network hermes-net" in run_cmd
    assert "--label hermes.citizen=1" in run_cmd
    assert "--label hermes.parent=main" in run_cmd
    assert "--label hermes.generation=1" in run_cmd
    assert "-v /proj/pop/x/dna:/dna:ro" in run_cmd          # DNA read-only
    assert f"-v {project.workspace_dir}:/workspace" in run_cmd  # shared workspace
    assert "sleep infinity" in run_cmd


def test_write_dna_files(project):
    d = village.write_dna(project, "hermes-p-w-g1-01", "do the thing",
                          parent="main", generation=1, run_id="0001",
                          siblings=["hermes-p-w-g1-01", "sib-a"], role="worker")
    from pathlib import Path
    dna = Path(d)
    lineage = json.loads((dna / "lineage.json").read_text())
    relations = json.loads((dna / "relations.json").read_text())
    assert lineage["parent"] == "main" and lineage["generation"] == 1
    assert "do the thing" in (dna / "brief.md").read_text()
    # A citizen is not its own sibling.
    assert "hermes-p-w-g1-01" not in relations["addressable"]
    assert "sib-a" in relations["addressable"]


# ---- harvest -----------------------------------------------------------------

def test_harvest_reads_before_it_removes(project):
    ep = DockerFakeEp()
    dest = village.harvest(ep, project, "hermes-p-w-g1-01", runtime="docker",
                           report="found the bug", thinking='{"content":"hmm"}')
    # Ordering is the invariant: logs + inspect must precede rm.
    kinds = [("logs" if " logs " in c else "inspect" if " inspect " in c
              else "rm" if " rm -f " in c else "other") for c in ep.calls]
    assert "logs" in kinds and "inspect" in kinds and "rm" in kinds
    assert kinds.index("logs") < kinds.index("rm")
    assert kinds.index("inspect") < kinds.index("rm")
    from pathlib import Path
    p = Path(dest)
    assert (p / "logs.txt").read_text().strip() == "citizen stdout+stderr"
    assert (p / "inspect.json").exists()
    assert (p / "report.md").read_text().strip() == "found the bug"
    assert (p / "thinking.jsonl").exists()


def test_reap_all_harvests_every_citizen(project, monkeypatch):
    ep = DockerFakeEp()
    monkeypatch.setattr(village, "list_citizens",
                        lambda *a, **k: ["cit-a", "cit-b"])
    n = village.reap_all(ep, project, runtime="docker")
    assert n == 2
    removed = [c for c in ep.calls if " rm -f " in c]
    assert any("cit-a" in c for c in removed)
    assert any("cit-b" in c for c in removed)


# ---- village_usable (the DinD guard) -----------------------------------------

def test_village_usable_true_when_daemon_allows_networks():
    ep = DockerFakeEp()
    ok, _ = village.village_usable(ep, runtime="docker")
    assert ok


def test_village_usable_false_when_network_create_fails():
    class Denies(DockerFakeEp):
        def run(self, command, timeout=120, stdin=None):
            self.calls.append(command)
            if "network create" in command:
                return (1, "", "operation not permitted")
            return (0, "docker", "")
    ok, detail = village.village_usable(Denies(), runtime="docker")
    assert not ok and "not permitted" in detail
