#!/usr/bin/env bash
# Deploy the SME Financing chat agent to Cloud Run — same project/region as the
# other BMMB AI services. Auth: `gcloud auth login` + ADC already configured.
#
#   ./deploy.sh
#
# Chat imports itself as the package `services.chat` (see api.py's module
# docstring), so the Docker build context is the repo root, not this
# directory -- `gcloud run deploy --source` can't point at a Dockerfile
# outside its source dir, so this builds + pushes the image directly instead
# (same as .github/workflows/deploy-chat.yml).
#
# Result URL (stable per project+region): https://chat-service-jkqy6qhfxa-as.a.run.app
set -euo pipefail

REGION=asia-southeast1
PROJECT_ID=prototype-bmmb-1b62
REPO_NAME=bmmb-ai-service
SERVICE_NAME=chat-service
TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:$(date +%Y%m%d-%H%M%S)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker build -f "$REPO_ROOT/services/chat/Dockerfile" -t "$TAG" "$REPO_ROOT"
docker push "$TAG"

gcloud run deploy "$SERVICE_NAME" \
  --image "$TAG" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 \
  --set-env-vars "LLM_BACKEND=vertex,GCP_PROJECT_ID=prototype-bmmb-1b62,VERTEX_LOCATION=asia-southeast1,MODEL_ID=gemini-2.5-flash,RAG_BACKEND=stub,AUDIT_BACKEND=memory,SESSION_STORE_BACKEND=none,EXTRACTION_BACKEND=stub,ALLOWED_ORIGINS=*"
