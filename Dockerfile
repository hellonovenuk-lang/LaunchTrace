# LaunchTrace website and pipeline.
# One image serves the site and runs the weekly pipeline, so there is a single
# thing to deploy and a single thing to keep up to date.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY docs/ ./docs/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY data/journals/ ./data/journals/

# Run as a non-root user.
RUN useradd --create-home --uid 10001 launchtrace \
    && mkdir -p /app/data/local /app/data/cache /app/reports \
    && chown -R launchtrace:launchtrace /app
USER launchtrace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
