"""The Village: embodied sub-agents as citizen containers on a shared dome.

A delegated child can be given a *body* — its own long-lived Docker container on a
user-defined network (the "dome"), carrying its DNA (lineage + relations), and
harvested to the file system when its life ends. This is the natural next step for
`delegate`, which today runs children in-process as bodiless ghosts.

Everything here is driven as shell strings through an endpoint's `.run()` (the same
`(rc, out, err)` contract `exec.py` uses) — no docker-py, matching the stdlib bias.
It reuses `exec._run_container_cmd` / `exec._container_exists` so there is one
`docker run` code path for both the shared exec box and citizen bodies.

Air-gap: the dome is created with `docker network create --internal`. On an internal
network the embedded DNS resolver still lets citizens reach each other *by container
name*, but there is no gateway and no NAT — so there is **no route to the internet**.
The external air-gap is preserved (kernel-enforced, like `--network none`) while
intra-village traffic becomes possible. When `village_enabled` is off, none of this
runs: the exec sandbox stays exactly `--network none` and delegation stays in-process.

Covenant: *where there was data, there will be data.* A citizen is never removed
before it is harvested — its logs, its inspect record, its report, and its inner
voice are written under `<project>/population/<name>/` first.
"""

from __future__ import annotations

import json
import re
import shlex
import time

from hermes.sandbox import exec as _exec
from hermes.sandbox import probe_container_runtime


def network_name(cfg) -> str:
    return str(cfg.get("village_network", "hermes-net") or "hermes-net")


def _slug(text: str, fallback: str = "x") -> str:
    """A DNS-label-safe slug: lowercase [a-z0-9-], no leading/trailing dash.

    The exec box's `container_name` permits uppercase and dots, which are NOT
    valid DNS labels — a citizen whose siblings must reach it *by name* needs a
    strict slug or name-resolution silently fails."""
    s = re.sub(r"[^a-z0-9-]", "-", (text or "").lower()).strip("-")
    return s or fallback


def citizen_name(project, role: str = "", gen: int = 1, n: int = 1) -> str:
    """A unique, DNS-safe citizen name: hermes-<proj>-<role>-g<gen>-<nn>.

    The name is also the container's address on the dome (Docker DNS), so it must
    be a valid DNS label and stay < 63 chars."""
    proj = _slug(getattr(project, "name", "") or "proj", "proj")
    role = _slug(role, "worker")
    # Bound the two variable parts so the whole name can't blow past 63 chars.
    stem = f"hermes-{proj[:24]}-{role[:16]}-g{int(gen)}-{int(n):02d}"
    return stem[:62].rstrip("-")


def network_exists(ep, name: str, runtime: str) -> bool:
    rc, out, _ = ep.run(
        f"{runtime} network ls --filter name=^{shlex.quote(name)}$ "
        f"--format '{{{{.Name}}}}'"
    )
    return rc == 0 and name in (out or "").split()


def ensure_network(ep, cfg, runtime: str = "") -> str:
    """Idempotently ensure the dome network exists; return its name.

    Internal by default (`--internal`): siblings resolve each other by name, but
    there is no route out. Raises SandboxError if the create fails."""
    from hermes.sandbox.provision import SandboxError, ensure_runtime

    if not runtime:
        runtime = ensure_runtime(ep)
    name = network_name(cfg)
    if network_exists(ep, name, runtime):
        return name
    internal = "--internal " if cfg.get("village_internal", True) else ""
    rc, out, err = ep.run(
        f"{runtime} network create {internal}"
        f"--label hermes.village=1 {shlex.quote(name)}",
        timeout=120,
    )
    if rc != 0 and not network_exists(ep, name, runtime):
        raise SandboxError(
            f"could not create the village network '{name}': "
            f"{(err or out).strip()[-300:]}")
    return name


def village_usable(ep, runtime: str = "") -> tuple[bool, str]:
    """Can this daemon actually create/remove networks? Cheap create+remove of a
    throwaway network — the guard for the Docker-in-Docker / no-NET_ADMIN case,
    where the harness must degrade gracefully to in-process delegation instead of
    dying deep in a run. Returns (ok, detail)."""
    if not runtime:
        runtime = probe_container_runtime(ep)
    if not runtime:
        return False, "no container runtime installed"
    probe = f"hermes-village-probe-{int(time.time())}"
    rc, out, err = ep.run(f"{runtime} network create --internal {probe}", timeout=60)
    detail = (err or out or "").strip()
    if rc != 0:
        return False, detail
    ep.run(f"{runtime} network rm {probe} 2>/dev/null || true")
    return True, "ok"


def _dna_dir(project, name: str):
    return project.population_dir / name / "dna"


def write_dna(project, name: str, brief: str, *, parent: str, generation: int,
              run_id, siblings=None, role: str = "") -> str:
    """Stamp the citizen's birth certificate on disk (mounted read-only at /dna).

    Two channels of lineage: this dir is for *code the citizen runs* to introspect
    itself and address siblings; the model's own lineage goes into its prompt. The
    dir lives under population/<name>/ so it survives as part of the harvest."""
    dna = _dna_dir(project, name)
    dna.mkdir(parents=True, exist_ok=True)
    siblings = list(siblings or [])
    lineage = {
        "name": name, "parent": parent, "generation": int(generation),
        "role": role, "project": getattr(project, "name", ""),
        "run": run_id, "birth_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    relations = {
        "parent": parent, "siblings": siblings,
        # Who this citizen may address by name on the dome (itself excluded).
        "addressable": [s for s in siblings if s != name],
    }
    (dna / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n")
    (dna / "relations.json").write_text(json.dumps(relations, indent=2) + "\n")
    (dna / "brief.md").write_text((brief or "").strip() + "\n")
    return str(dna)


def _citizen_labels(project, name: str, *, parent: str, generation: int,
                    role: str, run_id) -> list[str]:
    return [
        "hermes.citizen=1",
        f"hermes.project={_slug(getattr(project, 'name', '') or 'proj', 'proj')}",
        f"hermes.name={name}",
        f"hermes.parent={parent}",
        f"hermes.generation={int(generation)}",
        f"hermes.role={_slug(role, 'worker')}",
        f"hermes.run={run_id}",
    ]


def ensure_citizen(ep, project, name: str, cfg, *, parent: str, generation: int,
                   role: str, run_id, runtime: str = "", image: str = "",
                   dna_dir: str = "") -> tuple[str, str]:
    """Bring a citizen body up on the dome; return (runtime, name). Idempotent.

    Shares the project workspace mount (base design: embodiment is a distinct
    body + DNA + labels + harvest, not a separate filesystem, so `write_file` on
    the VPS and the citizen's `sandbox_shell` stay coherent). The DNA dir is
    mounted read-only at /dna."""
    from hermes.sandbox.provision import SandboxError, ensure_runtime

    if not runtime:
        runtime = ensure_runtime(ep)
    net = ensure_network(ep, cfg, runtime)
    if _exec._container_exists(ep, name, runtime):
        return runtime, name
    image = image or cfg.get("village_image", "") or cfg.get(
        "sandbox_image", _exec.DEFAULT_IMAGE)
    labels = _citizen_labels(project, name, parent=parent, generation=generation,
                             role=role, run_id=run_id)
    extra = [(dna_dir, "/dna", "ro")] if dna_dir else []
    cmd = _exec._run_container_cmd(
        name, runtime, str(project.workspace_dir), image,
        network=net, labels=labels, extra_mounts=extra,
    )
    rc, out, err = ep.run(cmd, timeout=600)
    if rc != 0 and not _exec._container_exists(ep, name, runtime):
        raise SandboxError(
            f"could not start citizen '{name}': {(err or out).strip()[-300:]}")
    return runtime, name


def harvest(ep, project, name: str, runtime: str = "", *, report: str = "",
            thinking: str = "") -> str:
    """Carry the body up the mountain, THEN bury it.

    Captures `docker logs` and `docker inspect` (both survive removal only if read
    first), the returned report, and the citizen's inner voice into
    population/<name>/ — then `docker rm -f`. Never raises: harvest runs in a
    `finally`, so even an errored or reaped body leaves a record. Returns the
    harvest dir path."""
    if not runtime:
        runtime = probe_container_runtime(ep) or "docker"
    dest = project.population_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    # Read the corpse BEFORE removing it.
    _, logs, logs_err = ep.run(f"{runtime} logs {shlex.quote(name)} 2>&1", timeout=60)
    (dest / "logs.txt").write_text(logs or logs_err or "")
    _, inspect, _ = ep.run(f"{runtime} inspect {shlex.quote(name)}", timeout=60)
    if inspect and inspect.strip():
        (dest / "inspect.json").write_text(inspect)
    if report and report.strip():
        (dest / "report.md").write_text(report.strip() + "\n")
    if thinking and thinking.strip():
        (dest / "thinking.jsonl").write_text(
            thinking if thinking.endswith("\n") else thinking + "\n")
    # Only now bury it.
    ep.run(f"{runtime} rm -f {shlex.quote(name)} 2>/dev/null || true")
    return str(dest)


def list_citizens(ep, project, runtime: str = "") -> list[str]:
    """Names of citizen containers (live or dead) belonging to this project."""
    if not runtime:
        runtime = probe_container_runtime(ep)
    if not runtime:
        return []
    proj = _slug(getattr(project, "name", "") or "proj", "proj")
    rc, out, _ = ep.run(
        f"{runtime} ps -a --filter label=hermes.citizen=1 "
        f"--filter label=hermes.project={shlex.quote(proj)} "
        f"--format '{{{{.Names}}}}'"
    )
    return [n for n in (out or "").split() if n] if rc == 0 else []


def reap_all(ep, project, runtime: str = "") -> int:
    """Harvest and remove every citizen of this project — crash cleanup and the
    `village reap` command. Returns how many were reaped."""
    if not runtime:
        runtime = probe_container_runtime(ep) or "docker"
    names = list_citizens(ep, project, runtime)
    for name in names:
        harvest(ep, project, name, runtime)
    return len(names)


def teardown_network(ep, cfg, runtime: str = "") -> None:
    """Remove the dome network (after all citizens are gone). Best-effort."""
    if not runtime:
        runtime = probe_container_runtime(ep) or "docker"
    ep.run(f"{runtime} network rm {shlex.quote(network_name(cfg))} "
           "2>/dev/null || true")
