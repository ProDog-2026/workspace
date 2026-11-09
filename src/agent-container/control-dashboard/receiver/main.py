import datetime as dt
import json
import uuid
from fastapi import FastAPI, Request

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def receive(request: Request, path: str):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk[:max(0, 8192 - len(data))])
        if len(data) >= 8192:
            break
    print(json.dumps({"event_id": uuid.uuid4().hex, "event_type": "request_received",
                      "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "path": "/" + path, "query": str(request.url.query),
                      "body": data.decode("utf-8", errors="replace")}, ensure_ascii=False), flush=True)
    return {"received": True}

