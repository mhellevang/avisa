# Playwright-basebilde: Python + Chromium + alle system-avhengigheter
# ferdig installert (matcher playwright==1.49.0 i requirements.txt).
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Avhengigheter først for bedre lag-caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite-fila lever i en mountet volum (se docker-compose.yml)
ENV DATABASE_URL=sqlite:////data/avisa.db
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
