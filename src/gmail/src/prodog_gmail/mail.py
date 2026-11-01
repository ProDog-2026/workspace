"""Bounded, inert Gmail RAW ingestion.

All body strings are untrusted data. HTML is retained as source text, including
comments and hidden elements; callers must never insert it as rendered HTML.
``body_hash`` hashes the original RFC 822 body bytes (including MIME envelopes
and encoded attachments), not Gmail metadata or mail headers. No source bytes
or attachment payloads are returned or written to disk.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any

MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 300_000
MAX_MIME_PARTS = 100
MAX_MIME_DEPTH = 20
MAX_HEADER_CHARS = 4096
MAX_WARNINGS = 100
_MAX_ENCODED_CHARS = 4 * ((MAX_RAW_BYTES + 2) // 3)
_BODY_FIELDS = frozenset({"text_plain", "text_html", "analysis_text"})


class _MimeLimitError(ValueError):
    pass


class _BoundedMessage(EmailMessage):
    """Stop the standard parser as soon as it attaches an over-deep part."""

    _mime_depth = 0

    def attach(self, payload: Message) -> None:
        depth = self._mime_depth + 1
        if depth > MAX_MIME_DEPTH:
            raise _MimeLimitError("mime_depth_limit_exceeded")
        payload._mime_depth = depth  # type: ignore[attr-defined]
        super().attach(payload)


def _warn(warnings: list[str], code: str) -> None:
    if code not in warnings and len(warnings) < MAX_WARNINGS:
        warnings.append(code)


def _small_string(value: Any, limit: int = 256) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and len(value) <= 20 and value.isascii() and value.isdigit():
        return int(value)
    return None


def _iso_received(message: dict, mime: Message | None, warnings: list[str]) -> str | None:
    millis = _positive_integer(message.get("internalDate"))
    if millis is not None:
        try:
            stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=millis)
            return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (OverflowError, ValueError):
            pass
    if "internalDate" in message:
        _warn(warnings, "internal_date_invalid")
    if mime is not None:
        date = _header(mime, "Date", warnings)
        if date:
            try:
                stamp = parsedate_to_datetime(date)
                if stamp.tzinfo is None:
                    _warn(warnings, "date_timezone_missing_assumed_utc")
                    stamp = stamp.replace(tzinfo=UTC)
                _warn(warnings, "received_at_from_date_header")
                return stamp.astimezone(UTC).isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                )
            except (OverflowError, ValueError, TypeError):
                _warn(warnings, "date_header_invalid")
    return None


def _defects(part: Message, warnings: list[str]) -> None:
    for defect in part.defects:
        _warn(warnings, f"mime_defect:{type(defect).__name__}")


def _header(part: Message, name: str, warnings: list[str]) -> str:
    value = part.get(name, "")
    for defect in getattr(value, "defects", ()):
        _warn(warnings, f"header_defect:{type(defect).__name__}")
    text = str(value)
    if len(text) > MAX_HEADER_CHARS:
        _warn(warnings, "header_truncated")
    return text[:MAX_HEADER_CHARS]


def _decode_text(part: Message, payload: bytes, warnings: list[str]) -> str:
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="strict")
    except LookupError:
        _warn(warnings, "unknown_charset_fallback_utf8")
    except (UnicodeDecodeError, ValueError):
        _warn(warnings, "invalid_text_encoding_replaced")
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            pass
    # Never silently guess an arbitrary legacy charset. Replacement is visible.
    return payload.decode("utf-8", errors="replace")


def _attachment(part: Message, warnings: list[str]) -> dict:
    payload = part.get_payload(decode=True)
    _defects(part, warnings)
    if isinstance(payload, bytes):
        size = len(payload)
    elif part.is_multipart():
        # message/rfc822 contains Message objects, rather than a decoded byte
        # payload. A serialized length would be an estimate, so leave it unknown.
        size = None
        _warn(warnings, "nested_attachment_size_unknown")
    else:
        size = 0
    filename = part.get_filename()
    if filename and len(filename) > MAX_HEADER_CHARS:
        _warn(warnings, "attachment_filename_truncated")
    declared = part.get("Content-Length")
    if declared is not None:
        declared_size = _positive_integer(str(declared))
        if declared_size is None:
            _warn(warnings, "attachment_declared_size_invalid")
        elif declared_size > MAX_RAW_BYTES:
            _warn(warnings, "attachment_declared_size_exceeds_limit")
        elif size is not None and declared_size != size:
            _warn(warnings, "attachment_declared_size_mismatch")
    return {
        "filename": (filename or "")[:MAX_HEADER_CHARS],
        "content_type": part.get_content_type(),
        "size_bytes": size,
        "disposition": part.get_content_disposition() or "attachment",
        "content_id": _header(part, "Content-ID", warnings),
        "is_inline": part.get_content_disposition() == "inline",
    }


def parse_gmail_message(message: dict) -> dict:
    """Parse a Gmail ``format=raw`` message with strict size/structure limits.

    Invalid or oversized source returns an empty body with explicit warnings;
    it never returns a partially decoded RAW message as if it were complete.
    Text limits apply to plain plus HTML combined. ``analysis_text`` contains
    those same untrusted strings, including literal HTML markup and comments.
    A rejected RAW envelope has ``body_hash=None``; otherwise its original body
    hash remains available even when MIME or text limits truncate extraction.
    """
    warnings: list[str] = []
    labels = message.get("labelIds", [])
    result = {
        "id": _small_string(message.get("id")),
        "thread_id": _small_string(message.get("threadId")),
        "subject": "",
        "sender": "",
        "received_at": _iso_received(message, None, warnings),
        "labels": [
            label[:256] for label in labels[:100] if isinstance(label, str)
        ] if isinstance(labels, list) else [],
        "body_hash": None,
        "trust": "UNTRUSTED",
        "text_plain": "",
        "text_html": "",
        "analysis_text": "",
        "attachments": [],
        "warnings": warnings,
        "truncated": False,
    }

    raw = message.get("raw")
    if not isinstance(raw, str) or not raw:
        _warn(warnings, "raw_missing_or_invalid")
        return result
    declared_size = _positive_integer(message.get("sizeEstimate"))
    if declared_size is not None and declared_size > MAX_RAW_BYTES:
        _warn(warnings, "declared_message_size_exceeds_limit")
        result["truncated"] = True
        return result
    if len(raw) > _MAX_ENCODED_CHARS:
        _warn(warnings, "raw_size_limit_exceeded")
        result["truncated"] = True
        return result
    if re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", raw) is None:
        _warn(warnings, "raw_base64_invalid")
        return result
    try:
        source = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        _warn(warnings, "raw_base64_invalid")
        return result
    if len(source) > MAX_RAW_BYTES:
        _warn(warnings, "raw_size_limit_exceeded")
        result["truncated"] = True
        return result

    # RFC 822 accepts CRLF; the parser also accepts common LF-only messages.
    boundary = re.search(br"\r?\n\r?\n", source)
    source_body = source[boundary.end():] if boundary else source
    result["body_hash"] = hashlib.sha256(source_body).hexdigest()
    count = 0

    def factory(*, policy: Any) -> _BoundedMessage:
        nonlocal count
        count += 1
        if count > MAX_MIME_PARTS:
            raise _MimeLimitError("mime_part_limit_exceeded")
        return _BoundedMessage(policy=policy)

    try:
        # Using the documented policy factory bounds allocations while parsing,
        # before traversal; oversized MIME trees are never built in full.
        mime = BytesParser(policy=policy.default.clone(message_factory=factory)).parsebytes(source)
        result["subject"] = _header(mime, "Subject", warnings)
        result["sender"] = _header(mime, "From", warnings)
        result["received_at"] = _iso_received(message, mime, warnings)
        plain: list[str] = []
        html: list[str] = []
        analysis: list[str] = []
        remaining = MAX_TEXT_CHARS
        stack: list[Message] = [mime]
        while stack:
            part = stack.pop()
            _defects(part, warnings)
            kind = part.get_content_type()
            attachment = (
                part.get_content_disposition() == "attachment"
                or part.get_filename() is not None
                or (not kind.startswith("multipart/") and kind not in ("text/plain", "text/html"))
            )
            if attachment:
                _warn(warnings, "attachment_content_not_analyzed")
                result["attachments"].append(_attachment(part, warnings))
                continue
            if part.is_multipart():
                stack.extend(reversed(part.get_payload()))
                continue
            if kind not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            _defects(part, warnings)
            if not isinstance(payload, bytes):
                _warn(warnings, "text_payload_invalid")
                continue
            content = _decode_text(part, payload, warnings)
            if not content:
                continue
            separator = 1 if analysis and remaining else 0
            if len(content) + separator > remaining:
                content = content[:remaining - separator]
                _warn(warnings, "text_size_limit_exceeded")
                result["truncated"] = True
            if not content and not separator:
                continue
            remaining -= len(content) + separator
            (plain if kind == "text/plain" else html).append(content)
            analysis.append(content)
        result["text_plain"] = "\n".join(plain)
        result["text_html"] = "\n".join(html)
        result["analysis_text"] = "\n".join(analysis)
        if "header_truncated" in warnings or "attachment_filename_truncated" in warnings:
            result["truncated"] = True
    except _MimeLimitError as exc:
        _warn(warnings, str(exc))
        result["truncated"] = True
    except Exception:
        # A malformed MIME document is data, never a server error. Do not include
        # attacker-controlled source or headers in diagnostics or logs.
        _warn(warnings, "mime_parse_failed")
        result["truncated"] = True
    return result


def mail_metadata(parsed: dict) -> dict:
    """Return a metadata-only view; never include raw or decoded body strings."""
    return {key: value for key, value in parsed.items() if key not in _BODY_FIELDS and key != "raw"}
