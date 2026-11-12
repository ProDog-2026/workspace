FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home receiver
COPY receiver/ receiver/
USER receiver
CMD ["uvicorn", "receiver.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

