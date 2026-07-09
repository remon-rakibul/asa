FROM python:3.13-slim

WORKDIR /app

# System deps for espeak-ng (voice layer) — optional but harmless to include.
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# WEB_CONCURRENCY sets the uvicorn worker count (default 1). With >1 worker,
# set REDIS_URL so rate limits are shared; the reminder loop is already safe
# (Postgres advisory lock ensures a single runner).
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-1}"]
