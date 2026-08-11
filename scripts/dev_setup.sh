#!/usr/bin/env bash
# Local dev bootstrap: venv (Python 3.12, matching the Dockerfile), install
# deps, run the test suite. Docker build/run is opt-in via --docker since it
# needs real Cloud SQL credentials filled into .env to do anything useful.
#
# Usage:
#   scripts/dev_setup.sh                 # venv + install + tests (extraction excluded)
#   scripts/dev_setup.sh --with-extraction   # also test services/extraction
#                                             # (requires GCP_PROJECT_ID, INSTANCE_CONNECTION_NAME,
#                                             #  DB_USER, DB_PASS, ADMIN_API_KEY already exported)
#   scripts/dev_setup.sh --docker        # also prep .env and build+run the Docker image
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

WITH_EXTRACTION=0
WITH_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --with-extraction) WITH_EXTRACTION=1 ;;
    --docker) WITH_DOCKER=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

echo "==> Checking for Python 3.12"
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 not found, installing via Homebrew"
  brew install python@3.12
fi

echo "==> Creating/activating .venv"
if [ ! -d .venv ]; then
  python3.12 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install -q "pytest==8.4.2" "reportlab==4.*" "requests==2.*" "openpyxl==3.*"

echo "==> Running test suite"
TEST_TARGETS=(services/aggregation services/bbox_generator services/chat services/mcp services/validation)
if [ "$WITH_EXTRACTION" -eq 1 ]; then
  TEST_TARGETS+=(services/extraction)
else
  echo "    (skipping services/extraction -- needs live GCP Cloud SQL creds; pass --with-extraction to include it)"
fi
pytest "${TEST_TARGETS[@]}" -q

if [ "$WITH_DOCKER" -eq 1 ]; then
  echo "==> Preparing .env"
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "    created .env from .env.example -- fill in real values before this will fully work"
  fi
  cp .env .env.bak
  # Docker's --env-file doesn't strip trailing '# comment' text like
  # python-dotenv does, so inline comments (e.g. "0.7 # Sheet 9.4...") get
  # passed through literally and crash float() parsing in chat's settings.
  sed -i '' -E 's/[[:space:]]+#.*$//' .env
  echo "    stripped inline comments from .env (backup saved as .env.bak)"

  # The container has no Application Default Credentials of its own, so
  # extraction's Cloud SQL Python Connector (config.py's default path) fails
  # with DefaultCredentialsError the moment it's used -- /health stays green
  # (doesn't touch the DB) but /extraction/templates 500s. Prefer mounting
  # the host's ADC into the container read-only (same connector code path
  # Cloud Run uses in prod, just via a mounted file instead of the attached
  # service account); only fall back to a host-side Cloud SQL Auth Proxy +
  # config.py's DB_HOST if ADC isn't actually set up locally.
  #
  # Array, guarded with the ${arr[@]+"${arr[@]}"} idiom below -- macOS's
  # default bash 3.2 treats an empty array expanded with "${arr[@]}" as an
  # unbound variable under `set -u` (fixed in bash 4.4+, but that's not
  # what ships as /bin/bash on macOS).
  DOCKER_EXTRA_ARGS=()

  ADC_FILE=""
  if command -v gcloud >/dev/null 2>&1 && gcloud auth application-default print-access-token >/dev/null 2>&1; then
    ADC_FILE="$(gcloud info --format='value(config.paths.global_config_dir)')/application_default_credentials.json"
    [ -f "$ADC_FILE" ] || ADC_FILE=""
  fi

  if [ -n "$ADC_FILE" ]; then
    echo "==> Found working Application Default Credentials -- mounting into container"
    DOCKER_EXTRA_ARGS+=(-v "$ADC_FILE:/tmp/adc.json:ro" -e "GOOGLE_APPLICATION_CREDENTIALS=/tmp/adc.json")
  else
    echo "==> No local ADC found (gcloud auth application-default login) -- falling back to a Cloud SQL Auth Proxy for extraction (127.0.0.1:5433)"
    DB_PROXY_PORT=5433
    services/extraction/scripts/db_proxy.sh "$DB_PROXY_PORT" > /tmp/dev_setup_db_proxy.log 2>&1 &
    DB_PROXY_PID=$!
    trap 'kill "$DB_PROXY_PID" 2>/dev/null' EXIT
    sleep 2
    DOCKER_EXTRA_ARGS+=(-e "DB_HOST=host.docker.internal" -e "DB_PORT=$DB_PROXY_PORT")
    # host.docker.internal reaches the proxy from inside the container --
    # works out of the box on Docker Desktop (Mac/Windows); Linux needs this.
    if [[ "$(uname -s)" == "Linux" ]]; then
      DOCKER_EXTRA_ARGS+=(--add-host=host.docker.internal:host-gateway)
    fi
  fi

  echo "==> Building Docker image"
  docker build -t unified-agents .

  echo "==> Running Docker image on :8080"
  docker run -p 8080:8080 ${DOCKER_EXTRA_ARGS[@]+"${DOCKER_EXTRA_ARGS[@]}"} \
    --env-file .env unified-agents
fi
