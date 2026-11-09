"""Launch both listeners in ONE process so the in-memory RunManager is shared."""
import argparse
import asyncio
import logging
import os
import ipaddress
import socket

import httpx
import uvicorn

from .api import create_dashboard_app
from .llm import LLMAdapter
from .runs import RunManager
from .worker_api import create_worker_app
from .runtime import DockerRuntime


class EmbeddedServer(uvicorn.Server):
    # Both servers are supervised by asyncio.run, which handles Ctrl+C.
    def capture_signals(self):
        from contextlib import nullcontext
        return nullcontext()


async def serve(args):
    if args.dashboard_port == args.worker_port:
        raise ValueError("Dashboard and worker ports must differ")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    cidr = os.getenv("WORK_NET_CIDR")
    if cidr and args.dashboard_host == "0.0.0.0":
        # Bind the management listener only to the external interface.
        import docker
        engine = docker.from_env(timeout=10)
        try:
            own = engine.containers.get(socket.gethostname())
            network = ipaddress.ip_network(cidr)
            addresses = [n["IPAddress"] for n in own.attrs["NetworkSettings"]["Networks"].values()
                         if n.get("IPAddress") and ipaddress.ip_address(n["IPAddress"]) not in network]
            if not addresses:
                raise RuntimeError("No management network interface found")
            args.dashboard_host = addresses[0]
        finally:
            engine.close()
    async with httpx.AsyncClient(timeout=300, trust_env=False) as client:
        manager = RunManager(LLMAdapter(client), runtime=DockerRuntime(),
                             data_root=os.getenv("DATA_ROOT", "../data/runs"))
        servers = [
            EmbeddedServer(uvicorn.Config(create_dashboard_app(manager),
                           host=args.dashboard_host, port=args.dashboard_port, access_log=False)),
            EmbeddedServer(uvicorn.Config(create_worker_app(manager),
                           host=args.worker_host, port=args.worker_port, access_log=False)),
        ]
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            await manager.close()
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=8000)
    parser.add_argument("--worker-host", default="127.0.0.1")
    parser.add_argument("--worker-port", type=int, default=8001)
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()

