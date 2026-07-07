# Single image shared by all services (worker, orchestrator, producer,
# interrupter). docker-compose picks the entrypoint per service.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cheapskate/ ./cheapskate/

# Default command runs a worker; overridden per-service in docker-compose.yml.
CMD ["python", "-m", "cheapskate.worker"]
