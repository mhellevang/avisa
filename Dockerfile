# Playwright base image: Python + Chromium + all system dependencies
# preinstalled (matches playwright==1.49.0 in requirements.txt).
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

ARG CODEX_VERSION=0.147.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build version (the git SHA) baked in by CI so the running container can report
# which build it is — see /status. Defaults to "dev" for local builds.
ARG BUILD_VERSION=dev
ENV BUILD_VERSION=${BUILD_VERSION}

# The SQLite file lives in a mounted volume (see docker-compose.yml)
ENV DATABASE_URL=sqlite:////data/avisa.db
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
