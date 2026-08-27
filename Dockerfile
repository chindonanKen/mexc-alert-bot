# Lightweight production image for the MEXC Alert Bot
FROM python:3.11-slim

WORKDIR /app

# System deps (curl for healthchecks if desired later)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY mexc_bot/ ./mexc_bot/

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Default alerts location inside container (override via env + volume)
ENV ALERTS_FILE=/app/data/alerts.json

# Optional build identity for GET /api/health (do not bake tokens)
ARG GIT_SHA=
ARG IMAGE_TAG=
ENV GIT_SHA=${GIT_SHA}
ENV IMAGE_TAG=${IMAGE_TAG}

# Run the bot
CMD ["python", "-m", "mexc_bot.main"]
