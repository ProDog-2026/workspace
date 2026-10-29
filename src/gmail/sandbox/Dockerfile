FROM python:3.12-slim

RUN useradd --uid 10001 --no-create-home sandbox \
    && mkdir /workspace && chown 10001:10001 /workspace
USER 10001:10001
WORKDIR /workspace
# The external controller enforces a shorter deadline; no API keys or host mounts.
CMD ["sleep", "660"]
