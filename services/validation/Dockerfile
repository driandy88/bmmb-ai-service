# Build context is the repo root, not this directory -- unlike
# services/extraction, this service's code imports itself as the package
# `services.validation` (see api.py's module docstring), so the `services/`
# parent has to exist in the image at the same relative path.
# Build with: docker build -f services/validation/Dockerfile -t <tag> .

FROM python:3.12-slim

WORKDIR /srv

COPY services/validation/requirements.txt ./services/validation/requirements.txt
RUN pip install --no-cache-dir -r services/validation/requirements.txt

COPY services/validation/ ./services/validation/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.validation.api:app --host 0.0.0.0 --port ${PORT}"]
