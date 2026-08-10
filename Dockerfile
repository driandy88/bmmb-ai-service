# Unified image for all 6 agent services (aggregation, bbox_generator, chat,
# extraction, mcp, validation), served behind the single FastAPI app in the
# repo-root main.py, which mounts each service's router under its own prefix.
#
# Build with: docker build -t unified-agents .

FROM python:3.12-slim

WORKDIR /srv

# OS-level deps needed by sub-services, previously split across their own
# per-service Dockerfiles (now Dockerfile.old, kept for reference):
#   tesseract-ocr           - services/bbox_generator: pytesseract OCR on image inputs
#   libimage-exiftool-perl  - services/extraction: exiftool subprocess for /extract-metadata
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
