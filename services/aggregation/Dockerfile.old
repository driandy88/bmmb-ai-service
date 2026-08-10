# Build context is the repo root, not this directory -- this service imports
# itself as the package `services.aggregation` (see api.py's module docstring),
# so the `services/` parent has to exist in the image at the same relative path.
# Build with: docker build -f services/aggregation/Dockerfile -t <tag> .

FROM python:3.12-slim

WORKDIR /srv

COPY services/aggregation/requirements.txt ./services/aggregation/requirements.txt
RUN pip install --no-cache-dir -r services/aggregation/requirements.txt

COPY services/__init__.py ./services/__init__.py
COPY services/aggregation/ ./services/aggregation/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.aggregation.api:app --host 0.0.0.0 --port ${PORT}"]
