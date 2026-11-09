FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY agent-container/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home agent && mkdir /work && chown agent:agent /work
COPY agent-container/ /app/
COPY control-dashboard/backend/tool_definitions.json /app/tool_definitions.json
USER agent
WORKDIR /work
CMD ["python", "/app/main.py"]

