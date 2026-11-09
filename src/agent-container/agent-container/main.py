import json
import sys
from agent import run_agent
from events import Events
from tools import Tools

def main():
    # Exactly one bounded JSON line. Do not echo stdin, tokens or exception bodies.
    try:
        job = json.loads(sys.stdin.buffer.readline(1024 * 1024))
        events = Events(job["run_id"], job["token"])
        result = run_agent(job, Tools(job["email"], job["work_dir"], job["receiver_url"]), events)
        return 0 if result == "completed" else 1
    except (ValueError, KeyError, TypeError, OSError):
        print("Invalid job input", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())

