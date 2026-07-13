#!/usr/bin/env bash
# Example curl requests against the validation API.
#
# Start the server first, from the repo root:
#   source venv/bin/activate
#   uvicorn services.validation.api:app --reload
#
# Then run this script (from the repo root):
#   ./examples/curl_requests.sh
#
# Each request wraps a bundle JSON file into the {"bundle": ...} shape the
# API expects, using python3 (no jq dependency). enable_ai_review is left
# false in every example so this works with no GCP/Vertex AI setup; drop
# that field (or set it true) once GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION
# are configured and you want the Gemini review step too.

set -euo pipefail

HOST="${HOST:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

make_payload() {
  # $1 = path to a bundle JSON file -> prints the {"bundle": ...} request body
  python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    bundle = json.load(f)
print(json.dumps({'bundle': bundle, 'enable_ai_review': False}))
" "$1"
}

echo "=== GET /health ==="
curl -s "$HOST/health"
echo -e "\n"

echo "=== POST /validate — sample_bundle_passing.json (expect overall_passed: true) ==="
make_payload "$SCRIPT_DIR/sample_bundle_passing.json" \
  | curl -s -X POST "$HOST/validate" -H "Content-Type: application/json" -d @- \
  | python3 -m json.tool
echo

echo "=== POST /validate — sample_bundle.json (expect some checks to fail) ==="
make_payload "$SCRIPT_DIR/sample_bundle.json" \
  | curl -s -X POST "$HOST/validate" -H "Content-Type: application/json" -d @- \
  | python3 -m json.tool
echo

echo "=== POST /validate — test2_conflict.json (adapter-mapping-bug demo bundle) ==="
make_payload "$SCRIPT_DIR/test2_conflict.json" \
  | curl -s -X POST "$HOST/validate" -H "Content-Type: application/json" -d @- \
  | python3 -m json.tool
echo

echo "=== POST /validate/from-extraction — extraction_results_example.json ==="
# This endpoint is the one to use from the extraction service: the request
# body is the RAW extraction dump keyed by template name
# (extracted_by_template), exactly what /extract returns per document -- no
# {"bundle": ...} wrapping needed. Overrides that extraction has no source
# for (loan terms, signature confirmation, etc.) are passed as query params.
FROM_EXTRACTION_URL="$HOST/validate/from-extraction?enable_ai_review=false&entity_type=Sdn%20Bhd&tenure_months=60&repayment_frequency=Monthly&signature_present=true"
python3 -c "
import json
with open('$SCRIPT_DIR/extraction_results_example.json') as f:
    raw = json.load(f)
raw.pop('_comment', None)
print(json.dumps(raw))
" | curl -s -X POST "$FROM_EXTRACTION_URL" \
    -H "Content-Type: application/json" \
    -d @- \
  | python3 -m json.tool
