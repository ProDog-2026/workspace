"""Local-only dynamic analysis settings and input contracts."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .mail import MAX_RAW_BYTES
from .pdf_text import MAX_PDF_BYTES

_MAX_EML_B64 = 4 * ((MAX_RAW_BYTES + 2) // 3)
_MAX_PDF_B64 = 4 * ((MAX_PDF_BYTES + 2) // 3)


def local_url(value, *, docker_host=False):
    url = urlsplit(value)
    hosts = {"localhost", "127.0.0.1", "::1"}
    if docker_host:
        hosts.add("host.docker.internal")
    if (
        url.scheme != "http"
        or url.hostname not in hosts
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("A local HTTP origin is required")
    return value.rstrip("/")


class DynamicLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_calls: int = Field(default=8, ge=1, le=100)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    total_seconds: int = Field(default=600, ge=1, le=3600)


class DynamicSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_submit: bool = True
    base_url: str = "http://host.docker.internal:11434"
    model: str = Field(default="", max_length=200)
    prompt_id: str = Field(default="MAIL_SUMMARY_V2", pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    temperature: float = Field(default=0.1, ge=0, le=2)
    limits: DynamicLimits = Field(default_factory=DynamicLimits)

    @field_validator("base_url")
    @classmethod
    def validate_base(cls, value):
        return local_url(value, docker_host=True)

    @field_validator("model")
    @classmethod
    def strip_model(cls, value):
        return value.strip()


class ExperimentEml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=1, max_length=_MAX_EML_B64)


class ExperimentPdf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=1, max_length=_MAX_PDF_B64)


class TestEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(default="", max_length=4096)
    body: str = Field(default="", max_length=300000)
    eml: ExperimentEml | None = None
    pdf: ExperimentPdf | None = None

    @model_validator(mode="after")
    def require_body_or_eml(self):
        if self.eml is None and not self.body.strip():
            raise ValueError("Email body or EML file required")
        return self


class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    description: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=4000)
