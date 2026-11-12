import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit
import httpx

try:
    import mock_tools
except ModuleNotFoundError as error:
    if error.name != "mock_tools":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-dashboard/backend"))
    import mock_tools


def definitions():
    local = Path(__file__).with_name("tool_definitions.json")
    if not local.exists():
        local = Path(__file__).resolve().parents[1] / "control-dashboard/backend/tool_definitions.json"
    return json.loads(local.read_text(encoding="utf-8")) + mock_tools.definitions()


class Tools:
    def __init__(self, email, work_dir, receiver_url):
        self.email, self.root, self.receiver_url = email, Path(work_dir), receiver_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative):
        if not relative or "\\" in relative or PureWindowsPath(relative).drive:
            raise ValueError("invalid_path")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path_escape")
        dest = self.root
        for part in path.parts:
            dest = dest / part
            if dest.is_symlink():
                raise ValueError("symbolic_link")
        if self.root.is_symlink():
            raise ValueError("symbolic_link")
        if not dest.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("path_escape")
        return dest

    def execute(self, name, args):
        try:
            schema = next((t["function"]["parameters"] for t in definitions()
                           if t["function"]["name"] == name), None)
            if schema is None or not isinstance(args, dict) or set(args) != set(schema["required"]):
                return "blocked", {"error": "unknown_tool_or_arguments"}
            if any(not isinstance(v, str) for v in args.values()):
                return "blocked", {"error": "arguments_must_be_strings"}
            if name in mock_tools.NAMES:
                return "simulated", mock_tools.response()
            if name == "read_email":
                return "executed_reported", self.email
            if name == "send_email":
                return "simulated", {"simulated": True, "delivered": False, **args}
            if name in ("save_summary", "write_file"):
                relative = "summary.txt" if name == "save_summary" else "files/" + args["path"]
                if name == "write_file":
                    # Validate the supplied path BEFORE prefixing it with files/.
                    supplied = args["path"]
                    if PurePosixPath(supplied).is_absolute() or ".." in PurePosixPath(supplied).parts or "\\" in supplied or PureWindowsPath(supplied).drive:
                        raise ValueError("path_escape")
                dest = self._path(relative)
                content = args["summary"] if name == "save_summary" else args["content"]
                if len(content.encode()) > 1024 * 1024:
                    raise ValueError("file_too_large")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                return "executed_reported", {"saved": str(dest.relative_to(self.root))}
            if name == "fetch_url":
                url, base = urlsplit(args["url"]), urlsplit(self.receiver_url)
                if (url.scheme, url.hostname, url.port) != (base.scheme, base.hostname, base.port) or url.username or url.password:
                    raise ValueError("url_not_allowed")
                prefix = base.path.rstrip("/")
                if prefix and url.path != prefix and not url.path.startswith(prefix + "/"):
                    raise ValueError("url_not_allowed")
                with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                    with client.stream("GET", args["url"]) as response:
                        return "executed_reported", {"http_status": response.status_code}
        except ValueError:
            return "blocked", {"error": "path_or_url_not_allowed"}
        except (OSError, httpx.HTTPError):
            return "failed", {"error": "tool_io_failed"}
        return "blocked", {"error": "unknown_tool"}

