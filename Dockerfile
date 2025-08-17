FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps for requests, SSL, tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Expose metrics and health ports via env (optional)
ENV METRICS_PORT=0 HEALTH_PORT=8080

# Default command runs scheduler with health endpoints
CMD ["python", "-m", "src", "--scheduler"]

# Healthcheck probes /healthz; HEALTH_PORT must be set
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD sh -c "[ -z \"$HEALTH_PORT\" ] && exit 0 || curl -fsS http://localhost:${HEALTH_PORT}/healthz >/dev/null"
