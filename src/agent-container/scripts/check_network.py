"""Run the control backend's shared recovery/isolation check after Compose is up."""
import json
import sys
import urllib.request
from pathlib import Path

def main():
    try:
        request = urllib.request.Request("http://127.0.0.1:8000/environment/check", method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=45) as response:
            result = json.load(response)
        result["verified"] = result.get("ready") is True
    except Exception:
        result = {"verified": False, "error": "Control API unavailable or diagnostic failed"}
    path=Path(__file__).resolve().parents[1]/"data/network-check.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
    return 0 if result.get("verified") else 1

if __name__=="__main__":
    sys.exit(main())

