# Build context is the repo root, not this directory -- this service imports
# itself as the package `services.extraction` (see api.py's module docstring),
# so the `services/` parent has to exist in the image at the same relative path.
# Build with: docker build -f services/extraction/Dockerfile -t <tag> .

FROM python:3.12-slim

WORKDIR /srv

# ExifTool binary for the /extract-metadata endpoint (invoked via subprocess).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

COPY services/extraction/requirements.txt ./services/extraction/requirements.txt
RUN pip install --no-cache-dir -r services/extraction/requirements.txt

COPY services/__init__.py ./services/__init__.py
COPY services/extraction/ ./services/extraction/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.extraction.api:app --host 0.0.0.0 --port ${PORT}"]
