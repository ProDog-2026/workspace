"""Local, fixed Compose deployment for the existing dynamic-analysis stack."""

import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .dynamic import ACTIVE
from .errors import ConnectorError

IMAGES = ("agent-container-control", "agent-container-receiver", "agent-container-worker:phase1")


class Deployment:
    def __init__(self, service, root=None):
        self.service = service
        self.root = Path(root) if root else Path(__file__).resolve().parents[3] / "agent-container"
        self.task = None
        self.state = "idle"
        self.error = None

    async def command(self, *args, timeout=20):
        def run():
            try:
                result = subprocess.run(
                    ["docker", *args],
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return result.returncode, result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                return 127, ""

        return await asyncio.to_thread(run)

    async def probe(self):
        docker_code, _ = await self.command("info", "--format", "{{.ServerVersion}}")
        result = {"docker": docker_code == 0, "images": False, "receiver": False, "control": False}
        if not result["docker"]:
            return result
        image_checks = await asyncio.gather(
            *(self.command("image", "inspect", image) for image in IMAGES)
        )
        result["images"] = all(code == 0 for code, _ in image_checks)
        code, health = await self.command(
            "inspect", "--format", "{{.State.Health.Status}}", "agent-test-receiver"
        )
        result["receiver"] = code == 0 and health == "healthy"
        code, state = await self.command(
            "inspect", "--format", "{{.State.Status}}", "agent-container-control-1"
        )
        if code == 0 and state == "running":
            try:
                prompts = await self.service.request("GET", "/prompts", timeout=3)
                result["control"] = isinstance(prompts, dict) and isinstance(
                    prompts.get("prompts"), list
                )
            except ConnectorError:
                pass
        return result

    async def status(self):
        if self.state == "running":
            return {"state": self.state, "stage": "building", "ready": False, "error": None}
        checks = await self.probe()
        return {
            "state": self.state,
            "stage": None,
            "ready": all(checks.values()),
            "checks": checks,
            "error": self.error,
        }

    async def start(self):
        async with self.service.lock:
            if self.state == "running":
                return {"state": "running", "stage": "building", "ready": False}
            if any(job["status"] in ACTIVE - {"queued"} for job in self.service.db.listing()):
                raise ConnectorError(
                    "dynamic_busy",
                    "진행 중이거나 상태를 확인할 수 없는 검사가 있습니다. 완료 후 다시 시도하세요.",
                    409,
                )
            url = urlsplit(getattr(self.service, "url", "http://127.0.0.1:8000"))
            if url.hostname not in {"127.0.0.1", "localhost"} or url.port != 8000:
                raise ConnectorError(
                    "deployment_url_mismatch",
                    "동적 검사 서버 주소를 기본 로컬 주소로 설정하세요.",
                    409,
                )
            checks = await self.probe()
            if not checks["docker"]:
                self.state, self.error = "failed", "docker_unavailable"
                return {"state": self.state, "ready": False, "checks": checks, "error": self.error}
            if all(checks.values()):
                self.state, self.error = "succeeded", None
                return {"state": self.state, "ready": True, "checks": checks, "error": None}
            code, workers = await self.command(
                "ps", "--filter", "label=agent.run_id", "--filter", "status=running", "-q"
            )
            if code != 0 or workers:
                raise ConnectorError(
                    "dynamic_busy", "실행 중인 검사 컨테이너를 먼저 확인하세요.", 409
                )
            if checks["control"]:
                try:
                    runs = await self.service.request("GET", "/runs", timeout=5)
                except ConnectorError as exc:
                    raise ConnectorError(
                        "dynamic_busy", "검사 서버의 실행 상태를 확인할 수 없습니다.", 409
                    ) from exc
                if not isinstance(runs, list) or any(
                    r.get("phase") != "finished"
                    and not (r.get("phase") == "manual" and r.get("status") != "running")
                    for r in runs
                ):
                    raise ConnectorError(
                        "dynamic_busy", "실험 서버에 진행 중인 검사가 있습니다.", 409
                    )
            self.state, self.error = "running", None
            self.service.deploying = True
            self.task = asyncio.create_task(self._run())
            return {"state": "running", "stage": "building", "ready": False}

    async def _run(self):
        try:
            code, _ = await self.command("compose", "up", "-d", "--build", timeout=1800)
            if code != 0:
                self.state, self.error = "failed", "deployment_failed"
                return
            for _ in range(20):
                checks = await self.probe()
                if all(checks.values()):
                    self.state, self.error = "succeeded", None
                    return
                await asyncio.sleep(1)
            self.state, self.error = "failed", "connection_failed"
        except Exception:
            self.state, self.error = "failed", "deployment_failed"
        finally:
            self.service.deploying = False
            self.service.wake.set()
