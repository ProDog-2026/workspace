import os
import pytest

def test_docker_archive_smoke():
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env(timeout=3)
        client.ping()
    except Exception:
        pytest.skip("Docker engine unavailable or access denied")
    if not os.getenv("RUN_DOCKER_TESTS"):
        client.close()
        pytest.skip("Set RUN_DOCKER_TESTS=1 after building worker image")
    container = None
    try:
        container = client.containers.run("agent-container-worker:phase1",
            command=["python", "-c", "from pathlib import Path; Path('/work/summary.txt').write_text('test')"],
            detach=True, network_mode="none", auto_remove=False)
        assert container.wait(timeout=15)["StatusCode"] == 0
        from backend.evidence import read_archive
        files, errors = read_archive(container.get_archive("/work")[0])
        assert files["summary.txt"] == "test" and not errors
    finally:
        if container:
            container.remove(force=True)
        client.close()

