FROM python:3.12-slim

WORKDIR /srv

# ExifTool binary for the /extract-metadata endpoint (invoked via subprocess).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
