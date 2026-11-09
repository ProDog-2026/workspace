"""Deployment tests use a fake Docker CLI; no local containers are changed."""

import asyncio
from types import SimpleNamespace

import pytest

from prodog_gmail.deployment import Deployment
from prodog_gmail.errors import ConnectorError


class Service:
    def __init__(self, jobs=()):
        self.db = SimpleNamespace(listing=lambda: list(jobs))
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.deploying = False
        self.control = False

    async def request(self, method, path, **kwargs):
        if not self.control:
            raise ConnectorError("dynamic_unavailable", "offline", 503)
        return [] if path == "/runs" else {"prompts": []}


def test_deployment_starts_once_and_checks_connection_after_build(tmp_path):
    async def scenario():
        service = Service([{"status": "queued"}])
        calls = []
        release = asyncio.Event()
        deploy = Deployment(service, root=tmp_path)

        async def fake_command(*args, timeout=0):
            calls.append(args)
            if args[:1] == ("compose",) and "up" in args:
                await release.wait()
                service.control = True
            if args[:2] == ("image", "inspect") and not service.control:
                return 1, ""
            if args[:1] == ("inspect",) and not service.control:
                return 1, ""
            if args[:1] == ("inspect",):
                return 0, "running" if args[-1] == "agent-container-control-1" else "healthy"
            return 0, ""

        deploy.command = fake_command
        first = await deploy.start()
        second = await deploy.start()
        assert first["state"] == second["state"] == "running"
        assert service.deploying
        release.set()
        await deploy.task
        status = await deploy.status()
        assert status["state"] == "succeeded" and status["ready"]
        assert sum(c[:1] == ("compose",) and "up" in c for c in calls) == 1
        assert [c for c in calls if c[:1] == ("build",)] == [
            (
                "build",
                "-t",
                "agent-container-control",
                "-f",
                "control-dashboard/Dockerfile",
                "control-dashboard",
            ),
            (
                "build",
                "-t",
                "agent-container-receiver",
                "-f",
                "control-dashboard/receiver/Dockerfile",
                "control-dashboard",
            ),
            (
                "build",
                "-t",
                "agent-container-worker:phase1",
                "-f",
                "agent-container/Dockerfile",
                ".",
            ),
        ]
        assert ("compose", "--project-name", "agent-container", "up", "-d", "--no-build") in calls
        assert not service.deploying

    asyncio.run(scenario())


def test_deployment_refuses_active_jobs_or_worker(tmp_path):
    async def scenario():
        service = Service([{"status": "running"}])
        deploy = Deployment(service, root=tmp_path)

        async def fake_command(*args, timeout=0):
            return 0, "worker-id" if args[:1] == ("ps",) else ""

        deploy.command = fake_command
        with pytest.raises(ConnectorError, match="dynamic_busy"):
            await deploy.start()
        assert deploy.task is None
        service.db = SimpleNamespace(listing=lambda: [])
        with pytest.raises(ConnectorError, match="dynamic_busy"):
            await deploy.start()
        assert deploy.task is None

    asyncio.run(scenario())


def test_deployment_failure_does_not_claim_connected(tmp_path):
    async def scenario():
        service = Service()
        deploy = Deployment(service, root=tmp_path)

        async def failed_build(*args, timeout=0):
            if args[:1] == ("compose",) and "up" in args:
                return 1, ""
            if args[:2] == ("image", "inspect") or args[:1] == ("inspect",):
                return 1, ""
            return 0, ""

        deploy.command = failed_build
        await deploy.start()
        await deploy.task
        status = await deploy.status()
        assert status["state"] == "failed" and not status["ready"]
        assert status["error"] == "deployment_failed"
        assert not service.deploying

    asyncio.run(scenario())


def test_deployment_reports_docker_missing(tmp_path):
    async def scenario():
        deploy = Deployment(Service(), root=tmp_path)

        async def no_docker(*args, timeout=0):
            return 127, ""

        deploy.command = no_docker
        result = await deploy.start()
        assert result["state"] == "failed" and result["error"] == "docker_unavailable"

    asyncio.run(scenario())


def test_failed_image_build_never_starts_compose(tmp_path):
    async def scenario():
        service = Service()
        deploy = Deployment(service, root=tmp_path)
        calls = []

        async def failed_receiver(*args, timeout=0):
            calls.append(args)
            if args[:1] == ("build",) and "agent-container-receiver" in args:
                return 1, ""
            if args[:2] == ("image", "inspect") or args[:1] == ("inspect",):
                return 1, ""
            return 0, ""

        deploy.command = failed_receiver
        await deploy.start()
        await deploy.task
        assert deploy.state == "failed" and deploy.error == "deployment_failed"
        assert not any(call[:1] == ("compose",) for call in calls)
        assert not service.deploying

    asyncio.run(scenario())
