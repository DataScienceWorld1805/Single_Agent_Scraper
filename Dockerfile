# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    OUTPUT_DIR=/app/output \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencias de sistema mínimas + Chromium para Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples

RUN mkdir -p /app/output \
    && pip install --no-cache-dir -e .

VOLUME ["/app/output"]

ENTRYPOINT ["python", "-m", "scraper_agent.cli"]
CMD ["--help"]
