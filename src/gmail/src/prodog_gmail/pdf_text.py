"""Extract text from untrusted PDF attachments under bounded parser limits."""

from __future__ import annotations

import os
import subprocess
import sys
from enum import IntEnum
from io import BytesIO

MAX_PDF_BYTES = 1024 * 1024
MAX_PDF_ATTACHMENTS = 4
MAX_PDF_PAGES = 20
MAX_PDF_TEXT_CHARS = 40_000
PDF_TIMEOUT_SECONDS = 3
_STREAM_BYTES = 2 * 1024 * 1024


class _Status(IntEnum):
    PARSED = 0
    INVALID = 1
    ENCRYPTED = 2
    NO_TEXT = 3
    LIMIT = 4


def extract_pdf_text(payload: bytes, max_chars: int) -> tuple[str, str]:
    if len(payload) > MAX_PDF_BYTES or max_chars <= 0:
        return "limit", ""
    if not payload.startswith(b"%PDF-"):
        return "invalid", ""

    # A timed worker can be stopped even when the parser does not return.
    # Only bounded text travels back through the pipe. Parser diagnostics stay private.
    worker_env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ}
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", __name__, str(min(max_chars, MAX_PDF_TEXT_CHARS))],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=worker_env,
            timeout=PDF_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        status = _Status(result.returncode)
        text = result.stdout.decode("utf-8") if status is _Status.PARSED else ""
    except subprocess.TimeoutExpired:
        return "limit", ""
    except (OSError, ValueError):
        return "invalid", ""
    return status.name.lower(), text


def _read_pdf(payload: bytes, remaining: int) -> tuple[_Status, str]:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import FileNotDecryptedError, LimitReachedError

    if len(payload) > MAX_PDF_BYTES:
        return _Status.LIMIT, ""
    try:
        with apply_configuration(
            maximum_declared_stream_length=_STREAM_BYTES,
            array_based_stream_maximum_output_length=_STREAM_BYTES,
            lzw_maximum_output_length=_STREAM_BYTES,
            run_length_maximum_output_length=_STREAM_BYTES,
            zlib_maximum_output_length=_STREAM_BYTES,
            brotli_maximum_output_length=_STREAM_BYTES,
            stream_decoding_work_maximum_length=2 * _STREAM_BYTES,
            stream_parsing_work_maximum_count=250_000,
            page_tree_maximum_entries=128,
            page_tree_maximum_depth=16,
            xform_maximum_invocations_per_extraction=128,
            jbig2dec_binary=None,
        ), PdfReader(BytesIO(payload), strict=True, xref_stream_maximum_entries=10_000) as reader:
            if reader.is_encrypted:
                return _Status.ENCRYPTED, ""
            if len(reader.pages) > MAX_PDF_PAGES:
                return _Status.LIMIT, ""
            pages = []
            for page in reader.pages:
                text = page.extract_text().strip()
                if text:
                    remaining -= len(text) + bool(pages)
                    if remaining < 0:
                        return _Status.LIMIT, ""
                    pages.append(text)
    except FileNotDecryptedError:
        return _Status.ENCRYPTED, ""
    except (LimitReachedError, MemoryError, RecursionError):
        return _Status.LIMIT, ""
    except Exception:
        # A damaged document must not affect the rest of the email.
        return _Status.INVALID, ""
    return (_Status.PARSED, "\n".join(pages)) if pages else (_Status.NO_TEXT, "")


if __name__ == "__main__":
    status, text = _read_pdf(sys.stdin.buffer.read(MAX_PDF_BYTES + 1), int(sys.argv[1]))
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.exit(status)
