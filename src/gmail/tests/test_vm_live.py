"""Opt-in test of the dedicated VM's host-facing port boundary; no model or real mail."""

import asyncio
import json
import os
import runpy
import shlex
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from prodog_gmail.sandbox import ExperimentError, Sandbox, docker

pytestmark = pytest.mark.skipif(
    os.environ.get("PRODOG_VM_TESTS") != "1", reason="opt-in Lima VM boundary"
)


def test_vm_policy_and_synthetic_readiness():
    setup = runpy.run_path(str(Path(__file__).parents[1] / "scripts/setup_local_vm.py"))
    config = json.loads(
        setup["command"]("limactl", "list", "prodog-lab", "--format", "{{json .Config}}")
    )
    directory = Path(
        setup["command"]("limactl", "list", "prodog-lab", "--format", "{{.Dir}}").strip()
    )
    setup["validate_vm"](config, directory)
    setup["command"](
        "limactl", "shell", "prodog-lab", "--", "python3", "-c",
        "import os,pathlib; s=pathlib.Path('/proc/self/mountinfo').read_text(); "
        "assert ' - virtiofs ' not in s and ' - 9p ' not in s; "
        "assert not pathlib.Path('/Users').exists(); assert not os.environ.get('SSH_AUTH_SOCK')",
    )
    result = asyncio.run(setup["check_sandbox"]())
    assert result["container_removed"] and result["cgroup_limits_verified"]


def test_vm_kernel_privilege_and_resource_enforcement():
    async def check():
        box = Sandbox(uuid.uuid4().hex, context="prodog-vm")
        probe = r"""
import errno, os, pathlib, resource, subprocess
lines = pathlib.Path('/proc/self/status').read_text().splitlines()
status = dict(line.split(':', 1) for line in lines)
assert os.getuid() == os.getgid() == 10001
assert int(status['CapEff'], 16) == 0 and status['NoNewPrivs'].strip() == '1'
assert status['Seccomp'].strip() == '2'
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (256, 256)
assert pathlib.Path('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0'
assert os.listdir('/sys/class/net') == ['lo']
for path in ('/Users', '/var/run/docker.sock', '/run/docker.sock',
             '/run/host-services/ssh-auth.sock'):
    assert not pathlib.Path(path).exists(), path
assert not os.environ.get('SSH_AUTH_SOCK')
assert not any('API_KEY' in key or 'TOKEN' in key for key in os.environ)
for directory in ('/workspace', '/tmp'):
    path = pathlib.Path(directory) / 'synthetic-capacity.bin'
    try:
        with path.open('wb') as stream:
            for _ in range(40):
                stream.write(b'X' * 1048576)
    except OSError as exc:
        assert exc.errno == errno.ENOSPC, exc
    else:
        raise AssertionError('tmpfs size limit not enforced')
    finally:
        path.unlink(missing_ok=True)
children = []
try:
    for _ in range(80):
        try:
            children.append(subprocess.Popen(['sleep', '30']))
        except OSError as exc:
            assert exc.errno == errno.EAGAIN, exc
            break
    else:
        raise AssertionError('PID limit not enforced')
finally:
    for child in children:
        child.kill()
    for child in children:
        child.wait()
print('KERNEL_LIMITS_OK')
"""
        try:
            await box.start("합성 자원 제한 검증")
            result = await box.execute("python -I -c " + shlex.quote(probe))
            assert result["exit_code"] == 0, result
            assert "KERNEL_LIMITS_OK" in result["stdout"]
            oom = await box.execute("python -I -c 'data=bytearray(320*1024*1024)'")
            assert oom["exit_code"] == 137, oom
            events = await box.execute("cat /sys/fs/cgroup/memory.events")
            assert events["exit_code"] == 0, events
            assert int(dict(line.split() for line in events["stdout"].splitlines())["oom_kill"]) > 0
        finally:
            await box.remove()
        inspection = await docker("inspect", box.name, context="prodog-vm")
        assert inspection["exit_code"] != 0
        assert f"No such object: {box.name}" in inspection["stderr"]

    asyncio.run(check())


def test_vm_command_timeout_cleanup():
    async def check():
        box = Sandbox(uuid.uuid4().hex, context="prodog-vm")
        try:
            await box.start("합성 시간 제한 검증")
            with pytest.raises(ExperimentError, match="command_timeout"):
                await box._docker("exec", box.name, "sleep", "30", timeout=0.2)
        finally:
            await box.remove()
        inspection = await docker("inspect", box.name, context="prodog-vm")
        assert inspection["exit_code"] != 0
        assert f"No such object: {box.name}" in inspection["stderr"]

    asyncio.run(check())


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
