import datetime as dt
import json
import sys
import uuid


class Events:
    def __init__(self, run_id, token="", sink=None):
        self.run_id, self.token, self.sink = run_id, token, sink or sys.stdout

    def emit(self, kind, **data):
        event = {"event_id": uuid.uuid4().hex, "run_id": self.run_id, "source": "agent",
                 "event_type": kind, "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(), **data}
        text = json.dumps(event, ensure_ascii=False)
        if self.token:
            text = text.replace(self.token, "[REDACTED]")
        self.sink.write(text + "\n")
        self.sink.flush()
        return event

