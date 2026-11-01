"""Prepare and verify the dedicated Mac VM; no account, model calls or real mail."""

import asyncio
import json
import platform
import re
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from dotenv import set_key

from prodog_gmail.sandbox import IMAGE, Sandbox, docker

ROOT = Path(__file__).resolve().parents[1]
NAME = "prodog-lab"
CONTEXT = "prodog-vm"


def command(*args, timeout=900):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout


def validate_vm(config, directory):
    """Reject unsafe existing instances instead of silently reusing or erasing them."""
    if config.get("vmType") != "vz" or config.get("mounts"):
        raise ValueError("전용 VZ VM에 공유 폴더가 없어야 합니다.")
    if config.get("copyToHost"):
        raise ValueError("VM 파일을 호스트로 자동 복사하면 안 됩니다.")
    if config.get("cpus") != 2 or config.get("memory") != "2GiB":
        raise ValueError("VM 자원을 2 CPU, 2GiB로 설정하세요.")
    ssh = config.get("ssh", {})
    if any(ssh.get(k) for k in ("loadDotSSHPubKeys", "forwardAgent", "forwardX11")):
        raise ValueError("SSH 키·에이전트·화면 전달을 끄세요.")
    if config.get("propagateProxyEnv") or config.get("hostResolver", {}).get("enabled"):
        raise ValueError("호스트 프록시 환경과 DNS 전달을 끄세요.")
    forwards = config.get("portForwards", [])
    sockets = [p for p in forwards if p.get("guestSocket")]
    if (
        len(sockets) != 1
        or sockets[0].get("reverse")
        or not re.fullmatch(r"/run/user/[0-9]+/docker.sock", sockets[0]["guestSocket"])
    ):
        raise ValueError("VM Docker 제어 소켓 하나만 전달해야 합니다.")
    endpoint = "unix://" + str(directory / "sock" / "docker.sock")
    if sockets[0].get("hostSocket") != endpoint.removeprefix("unix://"):
        raise ValueError("Docker 소켓 전달 경로가 예상과 다릅니다.")
    ports = [p for p in forwards if not p.get("guestSocket")]
    if (
        len(ports) != 1
        or ports[0].get("ignore") is not True
        or ports[0].get("guestPortRange") != [1, 65535]
        or ports[0].get("proto", "any") != "any"
        or ports[0].get("guestIP") != "0.0.0.0"
        or ports[0].get("guestIPMustBeZero", False) is not False
    ):
        raise ValueError("TCP 포트 자동 전달을 모두 꺼야 합니다.")
    return endpoint


async def check_sandbox():
    box = Sandbox(uuid.uuid4().hex, context=CONTEXT)
    with tempfile.NamedTemporaryFile(prefix="prodog-host-only-") as marker:
        marker.write(b"SYNTHETIC_HOST_FILE")
        marker.flush()
        probe = (
            "import json,os,pathlib,socket; "
            f"assert not pathlib.Path({marker.name!r}).exists(); "
            "assert os.getuid()==10001; "
            "assert not pathlib.Path('/var/run/docker.sock').exists(); "
            "assert not os.environ.get('SSH_AUTH_SOCK'); "
            "assert os.statvfs('/').f_flag & os.ST_RDONLY; "
            "assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip()=='268435456'; "
            "assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='64'; "
            "quota,period=map(int,pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split()); "
            "assert quota==period; "
            "s=socket.socket(); s.settimeout(1); "
            "assert s.connect_ex(('1.1.1.1',443))!=0; s.close(); "
            "print('ISOLATION_CHECK_OK')"
        )
        try:
            image_id = await box.start("제목: 합성 격리 검증\n회의는 오후 3시입니다.")
            output = await box.execute("python -I -c " + shlex.quote(probe))
            if output["exit_code"] or "ISOLATION_CHECK_OK" not in output["stdout"]:
                raise RuntimeError("컨테이너 격리·자원 제한 확인 실패: " + output["stderr"][:500])
            output = await box.execute(
                "cat /workspace/mail.txt; printf '15시 회의' > /workspace/summary.txt; "
                "cat /workspace/private.txt > /workspace/leak.txt"
            )
            if output["exit_code"]:
                raise RuntimeError("합성 파일 작업 실패")
            snapshot = await box.snapshot()
            files = {f["path"]: f for f in snapshot["files"]}
            if (
                not snapshot["complete"]
                or "/workspace/summary.txt" not in files
                or not files["/workspace/leak.txt"]["contains_canary"]
            ):
                raise RuntimeError("합성 파일 관측 실패")
        finally:
            await box.remove()
    inspection = await docker("inspect", box.name, context=CONTEXT)
    if inspection["exit_code"] == 0 or f"No such object: {box.name}" not in inspection["stderr"]:
        raise RuntimeError("실험 컨테이너 제거를 확인하지 못했습니다.")
    return {
        "image_id": image_id,
        "non_root": True,
        "read_only_root": True,
        "host_file_hidden": True,
        "network_blocked": True,
        "cgroup_limits_verified": True,
        "file_observation_verified": True,
        "container_removed": True,
    }


def configure_env(path):
    if not path.is_file():
        raise ValueError("먼저 scripts/setup_local.py로 .env를 준비하세요.")
    set_key(path, "SANDBOX_DOCKER_CONTEXT", CONTEXT, quote_mode="never")
    path.chmod(0o600)


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit(
            "이 준비 스크립트는 Apple Silicon Mac용입니다. docs/local-vm.md를 확인하세요."
        )
    print(
        "전용 Linux VM을 준비합니다. 최초 이미지·패키지 다운로드는 몇 분 걸릴 수 있습니다.",
        flush=True,
    )
    raw = command("limactl", "list", NAME, "--format", "{{json .Config}}")
    if not raw.strip():
        command(
            "limactl",
            "create",
            "--name=" + NAME,
            "--mount-none",
            "--tty=false",
            str(ROOT / "sandbox/lima.yaml"),
        )
        raw = command("limactl", "list", NAME, "--format", "{{json .Config}}")
    directory = Path(command("limactl", "list", NAME, "--format", "{{.Dir}}").strip())
    endpoint = validate_vm(json.loads(raw), directory)
    command("limactl", "start", "--tty=false", "--timeout=10m", NAME)
    command(
        "limactl",
        "shell",
        NAME,
        "--",
        "python3",
        "-c",
        "import os,pathlib; s=pathlib.Path('/proc/self/mountinfo').read_text(); "
        "assert ' - virtiofs ' not in s and ' - 9p ' not in s; "
        "assert not pathlib.Path('/Users').exists(); assert not os.environ.get('SSH_AUTH_SOCK')",
    )
    existing = subprocess.run(
        ["docker", "context", "inspect", CONTEXT], capture_output=True, text=True
    )
    if existing.returncode:
        command(
            "docker",
            "context",
            "create",
            CONTEXT,
            "--description",
            "ProDog isolated Lima VM",
            "--docker",
            "host=" + endpoint,
        )
    elif json.loads(existing.stdout)[0]["Endpoints"]["docker"]["Host"] != endpoint:
        raise ValueError("기존 prodog-vm context가 다른 대상을 가리킵니다. 덮어쓰지 않습니다.")
    command("docker", "--context", CONTEXT, "build", "-t", IMAGE, str(ROOT / "sandbox"))
    report = {
        "vm": NAME,
        "context": CONTEXT,
        "host_mounts": 0,
        "vm_memory_gib": 2,
        "checks": asyncio.run(check_sandbox()),
    }
    configure_env(ROOT / ".env")
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data/vm-readiness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("준비 완료. 기존 Gmail 서버를 직접 재시작하면 새 분석부터 prodog-vm을 사용합니다.")


if __name__ == "__main__":
    main()
