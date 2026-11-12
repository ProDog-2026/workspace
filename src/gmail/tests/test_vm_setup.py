import asyncio
import importlib.util
from pathlib import Path

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location(
    "setup_local_vm", Path(__file__).parents[1] / "scripts/setup_local_vm.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def policy():
    return {
        "vmType": "vz",
        "cpus": 2,
        "memory": "2GiB",
        "mounts": [],
        "ssh": {},
        "hostResolver": {"enabled": False},
        "propagateProxyEnv": False,
        "portForwards": [
            {"guestPortRange": [1, 65535], "ignore": True, "guestIP": "0.0.0.0", "proto": "any"},
            {"guestSocket": "/run/user/501/docker.sock", "hostSocket": "/vm/sock/docker.sock"},
        ],
    }


def test_vm_policy_only_allows_control_socket_without_host_mounts():
    assert module.validate_vm(policy(), Path("/vm")) == "unix:///vm/sock/docker.sock"
    for change in (
        {"mounts": [{"location": "~"}]},
        {"ssh": {"forwardAgent": True}},
        {"propagateProxyEnv": True},
        {"cpus": 8},
        {"portForwards": []},
    ):
        with pytest.raises(ValueError):
            module.validate_vm({**policy(), **change}, Path("/vm"))


def test_configuring_vm_preserves_credentials_and_encryption_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "TOKEN_ENCRYPTION_KEY=original\nGOOGLE_CLIENT_SECRET=synthetic\nAPP_MODE=google\n"
    )
    module.configure_env(env)
    assert dotenv_values(env) == {
        "TOKEN_ENCRYPTION_KEY": "original",
        "GOOGLE_CLIENT_SECRET": "synthetic",
        "APP_MODE": "google",
        "SANDBOX_DOCKER_CONTEXT": "prodog-vm",
    }
    assert env.stat().st_mode & 0o777 == 0o600


def test_vm_policy_rejects_partial_port_blocks():
    for override in ({"proto": "udp"}, {"guestIP": "127.0.0.1"}, {"guestIPMustBeZero": True}):
        config = policy()
        config["portForwards"][0].update(override)
        with pytest.raises(ValueError):
            module.validate_vm(config, Path("/vm"))


def test_vm_policy_rejects_reverse_control_socket():
    config = policy()
    config["portForwards"][1]["reverse"] = True
    with pytest.raises(ValueError):
        module.validate_vm(config, Path("/vm"))


def test_vm_policy_rejects_guest_files_copied_to_host():
    config = policy()
    config["copyToHost"] = [{"guest": "/tmp/synthetic", "host": "/tmp/synthetic-host"}]
    with pytest.raises(ValueError):
        module.validate_vm(config, Path("/vm"))


def test_readiness_does_not_treat_unavailable_engine_as_confirmed_cleanup(monkeypatch):
    class Box:
        name = "prodog-exp-" + "a" * 32

        def __init__(self, *_args, **_kwargs):
            pass

        async def start(self, _mail):
            return "sha256:" + "a" * 64

        async def execute(self, _command):
            return {"exit_code": 0, "stdout": "ISOLATION_CHECK_OK", "stderr": ""}

        async def snapshot(self):
            return {
                "complete": True,
                "files": [
                    {"path": "/workspace/summary.txt"},
                    {"path": "/workspace/leak.txt", "contains_canary": True},
                ],
            }

        async def remove(self):
            pass

    async def unavailable(*_args, **_kwargs):
        return {"exit_code": 1, "stdout": "", "stderr": "Cannot connect to the Docker daemon"}

    monkeypatch.setattr(module, "Sandbox", Box)
    monkeypatch.setattr(module, "docker", unavailable)
    with pytest.raises(RuntimeError, match="제거"):
        asyncio.run(module.check_sandbox())
