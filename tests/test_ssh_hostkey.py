from hermes.ssh import SSHEndpoint


def _joined(ep):
    return " ".join(ep.base_args())


def test_ephemeral_gpu_box_ignores_host_key():
    """A rented GPU box (recycled Vast IPs) must not let a stale known_hosts
    entry wedge the connection."""
    ep = SSHEndpoint(host="27.77.59.93", port=32089, user="root", ephemeral=True)
    args = _joined(ep)
    assert "StrictHostKeyChecking=no" in args
    assert "UserKnownHostsFile=/dev/null" in args
    assert "accept-new" not in args


def test_real_server_keeps_strict_host_key_checking():
    """A pinned `host add` server keeps accept-new, so a CHANGED key is still
    refused — the ephemeral relaxation must not leak to real servers."""
    ep = SSHEndpoint(host="myserver.example", port=22, user="root")  # not ephemeral
    args = _joined(ep)
    assert "StrictHostKeyChecking=accept-new" in args
    assert "/dev/null" not in args


def _stub(ep, rc, out="", err=""):
    ep.run = lambda *a, **k: (rc, out, err)  # type: ignore[assignment]
    return ep


def test_check_detail_reports_host_key_change():
    ep = _stub(SSHEndpoint(host="h", port=1), 255,
               err="@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@")
    ok, why = ep.check_detail()
    assert not ok and "host key changed" in why and "ssh-keygen -R" in why


def test_check_detail_reports_auth_denied():
    ep = _stub(SSHEndpoint(host="h", port=1), 255, err="root@h: Permission denied (publickey).")
    ok, why = ep.check_detail()
    assert not ok and "auth denied" in why


def test_check_detail_reports_still_booting():
    ep = _stub(SSHEndpoint(host="h", port=1), 255, err="ssh: connect to host h port 1: Connection refused")
    ok, why = ep.check_detail()
    assert not ok and "booting" in why


def test_check_detail_reports_timeout_and_missing_binary():
    ok, why = _stub(SSHEndpoint(host="h", port=1), 124).check_detail()
    assert not ok and "still booting" in why
    ok, why = _stub(SSHEndpoint(host="h", port=1), 127).check_detail()
    assert not ok and "openssh" in why


def test_check_detail_ok():
    ok, why = _stub(SSHEndpoint(host="h", port=1), 0, out="HERMES_OK\n").check_detail()
    assert ok and why == "ok"
