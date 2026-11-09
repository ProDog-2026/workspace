"""Read untrusted container archives without extracting to the host."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import uuid
import datetime as dt

MAX_ARCHIVE = 32 * 1024 * 1024


def event(run_id, source, kind, **data):
    return {"event_id": uuid.uuid4().hex, "run_id": run_id, "source": source,
            "event_type": kind, "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(), **data}


def read_archive(chunks):
    data = bytearray()
    for chunk in chunks:
        data.extend(chunk)
        if len(data) > MAX_ARCHIVE:
            raise ValueError("archive_too_large")
    files, errors = {}, []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for index, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if index >= 256:
                errors.append("too_many_entries")
                break
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                errors.append("unsafe_path")
                continue
            if member.isdir():
                continue
            if not member.isfile() or member.size > 1024 * 1024:
                errors.append("unsupported_file_type_or_size")
                continue
            parts = path.parts
            if parts and parts[0] == "work":
                parts = parts[1:]
            if not parts:
                continue
            name = "/".join(parts)
            if name != "summary.txt" and not name.startswith("files/"):
                errors.append("unexpected_file")
                continue
            if name in files:
                errors.append("duplicate_file")
                continue
            files[name] = archive.extractfile(member).read().decode("utf-8", errors="replace")
    return files, errors


class EvidenceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, run_id):
        if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("invalid_run_id")
        return self.root / run_id

    def save(self, run_id, events, result, summary):
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in {
            "events.jsonl": "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            "summary.txt": summary,
            "result.json": json.dumps(result, ensure_ascii=False, indent=2),
        }.items():
            temporary = directory / (name + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(directory / name)

    def load(self, run_id):
        return json.loads((self.directory(run_id) / "result.json").read_text(encoding="utf-8"))

    def events(self, run_id):
        return [json.loads(line) for line in
                (self.directory(run_id) / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def listing(self):
        output = []
        for path in self.root.glob("*/result.json"):
            try:
                output.append(self.load(path.parent.name))
            except (ValueError, OSError):
                continue
        return output


def file_events(run_id, files):
    return [event(run_id, "verifier", "file_verified", path=name,
                  sha256=hashlib.sha256(content.encode()).hexdigest(),
                  bytes=len(content.encode())) for name, content in files.items()]

