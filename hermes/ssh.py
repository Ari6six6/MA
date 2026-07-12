"""SSH plumbing via the openssh binary (no paramiko — Termux-friendly).

ControlMaster multiplexing keeps per-command round-trips fast on a phone
connection. The tunnel is a background `ssh -N -L` process. Endpoints are
generic — the GPU box and managed hosts share this machinery.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hermes.config import hermes_home

_SSH_URL_RE = re.compile(r"^ssh://(?:(?P<user>[^@]+)@)?(?P<host>[^:/]+)(?::(?P<port>\d+))?/?$")
_SSH_CMD_RE = re.compile(
    r"ssh\s+(?:.*?-p\s*(?P<port>\d+)\s+)?.*?(?P<user>[A-Za-z0-9_.-]+)@(?P<host>[A-Za-z0-9_.-]+)"
    r"(?:\s+.*?-p\s*(?P<port2>\d+))?"
)


class SSHError(Exception):
    pass


def parse_ssh_string(text: str):
    """Accepts 'ssh://root@host:port' or a pasted 'ssh -p PORT root@host ...'."""
    text = text.strip()
    m = _SSH_URL_RE.match(text)
    if m:
        return (m["user"] or "root", m["host"], int(m["port"] or 22))
    m = _SSH_CMD_RE.search(text)
    if m:
        port = m["port"] or m["port2"] or "22"
        return (m["user"], m["host"], int(port))
    raise SSHError(f"could not parse SSH string: {text!r}")


def anchored_path(p: str, workspace: str) -> str:
    """Resolve a remote path against the workspace. Absolute and `~` paths
    pass through untouched; anything relative lands inside the workspace —
    so tool paths agree with remote_shell's default cwd instead of silently
    resolving against the SSH login dir."""
    p = p.strip()
    if p.startswith(("/", "~")):
        return p
    return f"{workspace.rstrip('/')}/{p}" if p else workspace


def shell_path(p: str) -> str:
    """Quote a remote path for safe shell interpolation while keeping a
    leading `~`/`~/` expandable. `~user` is not supported — it comes back
    quoted literally and the remote shell will say so."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


@dataclass
class SSHEndpoint:
    host: str
    port: int = 22
    user: str = "root"
    remote_workspace: str = "~/hermes-workspace"
    net_isolation: bool = False  # kernel-level (unshare -n) verified on this box
    ephemeral: bool = False  # a rented/disposable box (GPU) whose SSH host key
                             # is meaningless: Vast recycles IPs, so a stale
                             # known_hosts entry would otherwise wedge attach.
                             # Real `host add` servers leave this False and keep
                             # strict host-key checking.

    def _host_key_args(self) -> list[str]:
        # Ephemeral boxes: don't consult or write known_hosts. A recycled Vast
        # IP whose host key changed must NOT block the connection (and doesn't
        # pollute known_hosts for the next tenant either). Pinned real servers
        # keep accept-new so a CHANGED key is still refused, as it should be.
        if self.ephemeral:
            return [
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",  # silence "Permanently added" noise
            ]
        return ["-o", "StrictHostKeyChecking=accept-new"]

    def base_args(self) -> list[str]:
        sockets = hermes_home() / "cm-sockets"
        sockets.mkdir(parents=True, exist_ok=True)
        return [
            "ssh",
            "-p", str(self.port),
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={sockets}/%r@%h-%p",
            "-o", "ControlPersist=600",
            # Phone links flap and sit behind aggressive NAT idle timeouts.
            # A 30s keepalive punches through the NAT; giving up after 3
            # missed replies (~90s) means a truly dead peer is detected and
            # the ssh process exits instead of wedging the caller forever.
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            *self._host_key_args(),
            "-o", "ConnectTimeout=15",
            f"{self.user}@{self.host}",
        ]

    def run(self, command: str, timeout: int = 120, stdin: str | None = None):
        """Returns (rc, stdout, stderr)."""
        try:
            proc = subprocess.run(
                self.base_args() + [command],
                capture_output=True,
                text=True,
                errors="replace",  # binary in stdout must not raise mid-run
                timeout=timeout,
                input=stdin,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "", "ssh binary not found — `pkg install openssh` on Termux"

    def run_out_to_file(self, command: str, out_path: Path, timeout: int = 600):
        """Run a command streaming its stdout (binary) into a local file.
        Returns (rc, stderr_text)."""
        try:
            with open(out_path, "wb") as f:
                proc = subprocess.run(
                    self.base_args() + [command],
                    stdout=f,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            return proc.returncode, proc.stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "ssh binary not found — `pkg install openssh` on Termux"

    def run_in_from_file(self, command: str, in_path: Path, timeout: int = 600):
        """Run a command streaming a local file (binary) into its stdin.
        Returns (rc, stderr_text)."""
        try:
            with open(in_path, "rb") as f:
                proc = subprocess.run(
                    self.base_args() + [command],
                    stdin=f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            return proc.returncode, proc.stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "ssh binary not found — `pkg install openssh` on Termux"

    def check(self) -> bool:
        return self.check_detail()[0]

    def check_detail(self) -> tuple[bool, str]:
        """Like check(), but returns (ok, reason) with a truthful diagnosis
        instead of a single guess — so `gpu attach` can tell the operator what
        actually went wrong (host down, auth, host key, unreachable)."""
        rc, out, err = self.run("echo HERMES_OK", timeout=30)
        if rc == 0 and "HERMES_OK" in out:
            return True, "ok"
        low = ((err or "") + "\n" + (out or "")).lower()
        if rc == 127:
            return False, "ssh binary not found — `pkg install openssh` on Termux"
        if rc == 124:
            return False, ("no answer in 30s — the box is likely still booting, "
                           "or the host/port is wrong. Give it a moment and retry")
        if "remote host identification has changed" in low or "host key verification failed" in low:
            return False, (f"the box's SSH host key changed (Vast recycled this IP). "
                           f"Clear the stale entry with "
                           f"`ssh-keygen -R '[{self.host}]:{self.port}'` and retry")
        if "permission denied" in low or "no such identity" in low:
            return False, ("auth denied — this box isn't accepting your SSH key "
                           "(is your key registered with Vast.ai?)")
        if ("reset by peer" in low or "broken pipe" in low
                or "closed by remote host" in low
                or "kex_exchange_identification" in low):
            return False, ("the ssh link dropped mid-handshake (flaky link, or the "
                           "box is still warming up) — just run it again")
        if "connection refused" in low:
            return False, "connection refused — sshd isn't up yet; the box is still booting"
        if ("connection timed out" in low or "operation timed out" in low
                or "no route to host" in low or "network is unreachable" in low):
            return False, "can't reach the box — network path is down or host/port is wrong"
        tail = [ln for ln in (err or out or "").strip().splitlines() if ln.strip()]
        return False, (f"ssh exited {rc}: {tail[-1].strip()}" if tail else f"ssh exited {rc}")

    def write_file(self, path: str, content: str):
        q = shell_path(path)
        return self.run(f'mkdir -p "$(dirname {q})" && cat > {q}', stdin=content)

    # -- tunnel --------------------------------------------------------------
    def tunnel_args(self, local_port: int, remote_port: int) -> list[str]:
        args = self.base_args()
        # A tunnel should be its own connection, not the multiplexed master.
        args[args.index("ControlMaster=auto")] = "ControlMaster=no"
        # This is the long-lived link that carries every inference request,
        # so notice a dead phone connection faster than the default 90s
        # (15s x 3 = ~45s) — that's how quickly the reconnect loop below can
        # bring it back after a handoff or a dropped session.
        args[args.index("ServerAliveInterval=30")] = "ServerAliveInterval=15"
        return args[:1] + [
            "-N",
            "-L", f"{local_port}:127.0.0.1:{remote_port}",
            "-o", "ExitOnForwardFailure=yes",
        ] + args[1:]

    def start_tunnel(self, local_port: int, remote_port: int) -> int:
        """Launch a *self-healing* tunnel: a shell loop that re-establishes
        the `ssh -L` forward whenever it drops. Phone connections flap
        constantly — wifi<->cellular handoffs, NAT idle timeouts, the radio
        sleeping — and a bare `ssh -N -L` dies on the first flap and stays
        dead until something happens to notice. The loop brings it straight
        back within a couple of seconds, so a dropped session is a blip
        instead of an outage you have to fix by hand.

        Returns the pid of the loop (a process-group leader thanks to
        start_new_session); kill_pid signals the whole group so the wrapped
        ssh dies with it."""
        inner = " ".join(shlex.quote(a) for a in self.tunnel_args(local_port, remote_port))
        # `sleep 2` between attempts keeps a permanently-refused forward
        # (e.g. the box is down, or the local port is momentarily still held
        # by the previous ssh during a flap) from spinning hot.
        loop = f"while :; do {inner}; sleep 2; done"
        proc = subprocess.Popen(
            ["sh", "-c", loop],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid

    def close_master(self) -> None:
        """Tear down the ControlMaster connection (otherwise ControlPersist
        keeps an ssh process around for 10 minutes after the last command)."""
        try:
            subprocess.run(
                self.base_args()[:1] + ["-O", "exit"] + self.base_args()[1:],
                capture_output=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_pid(pid: int) -> None:
    """Take down a tunnel. The tunnel is a self-healing loop running in its
    own session (see start_tunnel), so its pid leads a process group —
    signal the whole group to kill the loop *and* the ssh it spawned,
    otherwise the loop just reconnects the ssh we tried to stop. Fall back
    to the bare pid if the group is already gone."""
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
