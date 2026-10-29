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
