# Build context is the repo root, not this directory -- unlike
# services/extraction, this service's code imports itself as the package
# `services.bbox_generator` (see api.py's module docstring), so the
# `services/` parent has to exist in the image at the same relative path.
# Build with: docker build -f services/bbox_generator/Dockerfile -t <tag> .

FROM python:3.12-slim

# pytesseract shells out to the tesseract binary -- it isn't a pip package,
# it has to exist on the image. Only used for image (OCR) inputs; PDFs never
# touch this.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY services/bbox_generator/requirements.txt ./services/bbox_generator/requirements.txt
RUN pip install --no-cache-dir -r services/bbox_generator/requirements.txt

COPY services/bbox_generator/ ./services/bbox_generator/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.bbox_generator.api:app --host 0.0.0.0 --port ${PORT}"]
