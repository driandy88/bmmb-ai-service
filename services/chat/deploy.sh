#!/usr/bin/env bash
# Deploy the SME Financing chat agent to Cloud Run — same project/region as the
# other BMMB AI services. Auth: `gcloud auth login` + ADC already configured.
#
#   ./deploy.sh
#
# Result URL (stable per project+region): https://chat-service-jkqy6qhfxa-as.a.run.app
set -euo pipefail

gcloud run deploy chat-service \
  --source . \
  --region asia-southeast1 \
  --project prototype-bmmb-1b62 \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 \
  --set-env-vars "LLM_BACKEND=vertex,GCP_PROJECT_ID=prototype-bmmb-1b62,VERTEX_LOCATION=asia-southeast1,MODEL_ID=gemini-2.5-flash,RAG_BACKEND=stub,AUDIT_BACKEND=memory,SESSION_STORE_BACKEND=none,EXTRACTION_BACKEND=stub,ALLOWED_ORIGINS=*"
