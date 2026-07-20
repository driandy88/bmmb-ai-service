# Build context is the repo root, not this directory -- this service imports
# itself as the package `services.mcp` (see api.py's module docstring), and it
# spawns servers/email_server.py as a subprocess, so the whole `services/mcp/`
# tree has to exist in the image at the same relative path.
# Build with: docker build -f services/mcp/Dockerfile -t <tag> .

FROM python:3.12-slim

WORKDIR /srv

COPY services/mcp/requirements.txt ./services/mcp/requirements.txt
RUN pip install --no-cache-dir -r services/mcp/requirements.txt

COPY services/__init__.py ./services/__init__.py
COPY services/mcp/ ./services/mcp/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.mcp.api:app --host 0.0.0.0 --port ${PORT}"]
