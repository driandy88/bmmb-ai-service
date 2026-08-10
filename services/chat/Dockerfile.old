# Build context is the repo root, not this directory -- this service imports
# itself as the package `services.chat` (see api.py's module docstring), so
# the `services/` parent has to exist in the image at the same relative path.
# Build with: docker build -f services/chat/Dockerfile -t <tag> .

FROM python:3.12-slim

WORKDIR /srv

COPY services/chat/requirements.txt ./services/chat/requirements.txt
RUN pip install --no-cache-dir -r services/chat/requirements.txt

COPY services/__init__.py ./services/__init__.py
COPY services/chat/ ./services/chat/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn services.chat.api:app --host 0.0.0.0 --port ${PORT}"]
