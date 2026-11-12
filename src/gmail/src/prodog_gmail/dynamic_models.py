"""Local-only dynamic analysis settings and input contracts."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class TestEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(default="", max_length=4096)
    body: str = Field(min_length=1, max_length=300000)

    @field_validator("body")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Email body required")
        return value


class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    description: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=4000)
