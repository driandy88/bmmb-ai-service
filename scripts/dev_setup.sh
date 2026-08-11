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

  echo "==> Building Docker image"
  docker build -t unified-agents .

  echo "==> Running Docker image on :8080"
  docker run -p 8080:8080 --env-file .env unified-agents
fi
