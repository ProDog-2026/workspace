"""Exercise a running local demo server without reading a real mailbox."""

import httpx

BASE = "http://127.0.0.1:8765"


def main():
    with httpx.Client(base_url=BASE, timeout=20) as client:
        health = client.get("/health")
        health.raise_for_status()
        if health.json().get("mode") != "demo":
            raise SystemExit("Skipped: this smoke script only runs in demo mode.")
        for path, content_type in [
            ("/", "text/html"),
            ("/static/app.js", "javascript"),
            ("/static/style.css", "text/css"),
        ]:
            response = client.get(path)
            response.raise_for_status()
            assert content_type in response.headers["content-type"]
            assert response.headers["cache-control"] == "no-store"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

        def post(path):
            session = client.get("/api/session").json()
            response = client.post(
                path, headers={"Origin": BASE, "X-CSRF-Token": session["csrf_token"]}
            )
            response.raise_for_status()
            return response.json()

        assert client.get("/api/mails").status_code == 401
        assert post("/api/demo/connect")["authenticated"]
        listing = client.get("/api/mails?limit=2").json()
        assert len(listing["messages"]) == 2
        following = client.get(
            "/api/mails", params={"limit": 2, "page_token": listing["next_page_token"]}
        ).json()
        messages = listing["messages"] + following["messages"]
        assert len(messages) == 3
        for metadata in messages:
            detail = client.get("/api/mails/" + metadata["id"])
            detail.raise_for_status()
            mail = detail.json()
            assert mail["analysis_text"] and mail["trust"] == "UNTRUSTED"
            assert "text_plain" not in metadata and "text_html" not in metadata
            if mail["attachments"]:
                assert "attachment_content_not_analyzed" in mail["warnings"]
        assert post("/api/disconnect")["disconnected"]
        assert client.get("/api/mails").status_code == 401
        print("PASS: static assets, headers, demo connection, pagination, 3 details, disconnect")


if __name__ == "__main__":
    main()
