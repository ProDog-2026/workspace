from __future__ import annotations

from collections.abc import Mapping
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any

MAX_PART_CHARS = 200_000


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(self.parts)


def _html_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        return parser.text()
    except Exception:
        return ""


def _decoded_text(part: Message) -> str:
    try:
        return part.get_content()[:MAX_PART_CHARS]
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")[:MAX_PART_CHARS]


def message_to_model_text(message: EmailMessage) -> str:
    sections = [
        f"[SUBJECT] {message.get('subject', '')}",
        f"[FROM] {message.get('from', '')}",
        f"[REPLY_TO] {message.get('reply-to', '')}",
        f"[TO] {message.get('to', '')}",
    ]

    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        filename = part.get_filename() or ""
        sections.append(f"[PART] type={content_type} filename={filename}")
        if content_type not in {"text/plain", "text/html"}:
            continue
        value = _decoded_text(part)
        if content_type == "text/html":
            sections.append(f"[HTML_RAW] {value}")
            sections.append(f"[HTML_TEXT] {_html_text(value)}")
        else:
            sections.append(f"[PLAIN] {value}")
    return "\n".join(sections)


def eml_bytes_to_model_text(raw: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    return message_to_model_text(message)


def _header_value(value: str) -> str:
    """Flatten a header value so attacker-controlled newlines cannot break storing it."""
    return " ".join(value.replace(chr(0), " ").split())


def fields_to_model_text(
    subject: str = "",
    body_text: str = "",
    body_html: str = "",
    sender: str = "",
    reply_to: str = "",
) -> str:
    message = EmailMessage()
    message["Subject"] = _header_value(subject or "")
    if sender:
        message["From"] = _header_value(sender)
    if reply_to:
        message["Reply-To"] = _header_value(reply_to)
    if body_html:
        message.set_content(body_text or "")
        message.add_alternative(body_html, subtype="html")
    else:
        message.set_content(body_text or "")
    return message_to_model_text(message)


def collect_api_to_model_text(mail: Mapping[str, Any]) -> str:
    """Convert the Gmail collector output without retaining the source record."""
    return fields_to_model_text(
        subject=str(mail.get("subject") or ""),
        sender=str(mail.get("sender") or ""),
        body_text=str(mail.get("text_plain") or mail.get("analysis_text") or ""),
        body_html=str(mail.get("text_html") or ""),
    )
