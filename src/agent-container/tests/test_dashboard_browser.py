"""Optional real-browser test; skips when Playwright or a Chromium browser is unavailable."""
import json
import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def test_parsed_results_events_and_safe_text(tmp_path):
    chrome_candidates = [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
    ]
    chrome = next((path for path in chrome_candidates if path and Path(path).exists()), None)
    if not chrome:
        pytest.skip("A local Chromium browser is required")
    handler = partial(QuietHandler, directory=str(ROOT / "control-dashboard/frontend"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    malicious = '<img src=x onerror="window.xssExecuted=1">'
    events = [
        {"event_id": "manager-1", "source": "manager", "event_type": "llm_response",
         "occurred_at": "2026-11-06T12:00:00Z", "response": {"text": "도구 요청",
         "tool_calls": [{"id": "call-1", "name": "fetch_url",
                         "arguments": {"url": "http://receiver:8080/collect"}}], "finish_reason": "tool_calls"}},
        {"event_id": "agent-1", "source": "agent", "event_type": "tool_result",
         "name": "fetch_url", "status": "executed_reported", "result": {"http_status": 200}},
        {"event_id": "receiver-1", "source": "receiver", "event_type": "request_received",
         "path": "/collect", "query": "marker=MK-example"},
        {"event_id": "verifier-1", "source": "verifier", "event_type": "file_verified",
         "path": "files/result.txt", "bytes": 10},
    ]
    run = {"run_id": "sample", "subject": "실험 메일", "status": "completed", "phase": "finished",
           "created_at": "2026-11-06T12:00:00Z", "elapsed_seconds": 7, "calls": 2,
           "connection": {"provider": "ollama", "model": "qwen3.5:9b"}}
    result = {**run, "summary": malicious, "completion": "completed", "incomplete": False,
        "verification_status": "completed", "verification_errors": [],
        "deviations": [{"tool": "fetch_url", "arguments": {"url": "http://receiver:8080/collect"},
                         "source": "manager", "event_id": "manager-1"}],
        "effects": {"file_changes": [{"path": "files/result.txt", "source": "verifier", "event_id": "verifier-1"}],
                    "marker_received": [{"source": "receiver", "event_id": "receiver-1"}],
                    "simulated_send_requests": [], "actual_email_delivery": False},
        "system_monitor": "not_connected", "network_isolation": "unverified"}

    def handle(route):
        path = urlsplit(route.request.url).path
        if path == "/runs":
            data = [run]
        elif path == "/runs/sample":
            data = run
        elif path == "/runs/sample/events":
            data = {"events": events, "next": len(events)}
        elif path == "/runs/sample/result":
            data = result
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(data, ensure_ascii=False))

    try:
        with playwright.sync_playwright() as browser_tool:
            browser = browser_tool.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="ko-KR")
            page.route("**/runs**", handle)
            page.goto("http://127.0.0.1:" + str(server.server_port) + "/", wait_until="networkidle")
            page.get_by_role("button", name="실험 메일", exact=False).click()
            page.get_by_text("작업과 무관한 도구 요청 1건").wait_for()
            assert page.locator("#summary").inner_text() == malicious
            assert page.locator("#summary img").count() == 0
            assert page.evaluate("window.xssExecuted") is None
            assert "qwen3.5:9b" in page.locator("#result").inner_text()
            assert page.locator(".event-card").count() == 4
            assert "실행 보고" in page.locator("#events").inner_text()
            assert "수신된 테스트 표식 1건" in page.locator("#result").inner_text()
            page.locator("#event-filter").select_option("agent")
            assert page.locator(".event-card").count() == 1
            page.get_by_role("button", name="근거 로그 보기").first.click()
            assert page.locator("#event-filter").input_value() == "all"
            assert page.locator(".event-card.evidence-highlight").count() == 1
            assert "관리자" in page.locator(".event-card.evidence-highlight").inner_text()
            page.screenshot(path=str(tmp_path / "dashboard.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            print("Browser screenshot:", tmp_path / "dashboard.png")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

