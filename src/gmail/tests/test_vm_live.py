"""Opt-in test of the dedicated VM's host-facing port boundary; no model or real mail."""

import os
import socket
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PRODOG_VM_TESTS") != "1", reason="opt-in Lima VM boundary"
)


def test_vm_wildcard_listener_is_not_forwarded_to_host():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    source = (
        "import http.server,threading; "
        f"s=http.server.HTTPServer(('0.0.0.0',{port}),http.server.BaseHTTPRequestHandler); "
        "threading.Timer(10,s.shutdown).start(); print('READY',flush=True); "
        "s.serve_forever(); s.server_close()"
    )
    process = subprocess.Popen(
        ["limactl", "shell", "prodog-lab", "--", "python3", "-c", source],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        for _ in range(20):
            with socket.socket() as connection:
                connection.settimeout(0.2)
                assert connection.connect_ex(("127.0.0.1", port)) != 0, "VM port reached the host"
            time.sleep(0.3)
        process.communicate(timeout=12)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=12)
