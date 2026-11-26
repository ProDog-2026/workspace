"""Validate transient user-provided mail and scan it using the Gmail pipeline."""

import base64
import binascii
import re

from pydantic import BaseModel, ConfigDict, Field

from .errors import ConnectorError
from .mail import MAX_RAW_BYTES, mail_metadata, parse_gmail_message
from .pdf_text import MAX_PDF_BYTES
from .scan import scan_mail


class UploadFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=1, max_length=4 * ((MAX_RAW_BYTES + 2) // 3))


class FileScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eml: UploadFile
    pdf: UploadFile | None = None


def decode_file(upload: UploadFile, extension: str, limit: int) -> bytes:
    if not upload.filename.lower().endswith(extension):
        raise ConnectorError("invalid_file", f"{extension} 파일을 선택하세요.", 422)
    if len(upload.data) > 4 * ((limit + 2) // 3):
        raise ConnectorError("file_limit", "파일 크기 제한을 초과했습니다.", 422)
    try:
        data = base64.b64decode(upload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConnectorError("invalid_file", "파일을 읽을 수 없습니다.", 422) from exc
    if len(data) > limit:
        raise ConnectorError("file_limit", "파일 크기 제한을 초과했습니다.", 422)
    return data


def scan_file(body: FileScanInput, detector):
    raw = decode_file(body.eml, ".eml", MAX_RAW_BYTES)
    # RFC 5322 messages have at least one header field followed by a blank line.
    if not re.match(br"[!-9;-~]+:[^\r\n]*\r?\n", raw) or not re.search(br"\r?\n\r?\n", raw):
        raise ConnectorError("invalid_eml", "올바른 EML 파일을 선택하세요.", 422)
    supplemental = None
    if body.pdf:
        supplemental = (body.pdf.filename, decode_file(body.pdf, ".pdf", MAX_PDF_BYTES))
    mail = parse_gmail_message({"raw": base64.urlsafe_b64encode(raw).decode()},
                               supplemental_pdf=supplemental)
    if mail["body_hash"] is None:
        raise ConnectorError("invalid_eml", "EML 파일을 읽을 수 없습니다.", 422)
    return {"mail": {**mail_metadata(mail), "detection": scan_mail(detector, mail)}}
