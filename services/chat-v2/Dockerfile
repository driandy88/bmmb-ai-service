# Mirrors services/chat/Dockerfile — same base, same layout, same entrypoint
# shape — so the only variable between v1 and v2 in the A/B is the approach,
# not the runtime.
FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Cloud Run injects $PORT at runtime; default 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]