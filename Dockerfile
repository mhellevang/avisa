# Playwright base image: Python + Chromium + all system dependencies
# preinstalled (matches playwright==1.49.0 in requirements.txt).
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The SQLite file lives in a mounted volume (see docker-compose.yml)
ENV DATABASE_URL=sqlite:////data/avisa.db
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
