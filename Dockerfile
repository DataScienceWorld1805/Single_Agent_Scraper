# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    OUTPUT_DIR=/app/output \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

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
EXPOSE 8000

# Por defecto: interfaz web. CLI: docker compose run --rm --entrypoint python scraper -m scraper_agent.cli ...
CMD ["python", "-m", "uvicorn", "scraper_agent.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
