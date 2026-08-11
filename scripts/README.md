# scripts/

## dev_setup.sh

Bootstraps a local dev environment and runs the test suite: creates a
Python 3.12 venv (matching the Dockerfile's `python:3.12-slim`), installs
`requirements.txt` plus the dev-only test extras, then runs pytest across
the services that don't need live GCP credentials.

```bash
scripts/dev_setup.sh
```

What it does, in order:

1. Installs `python3.12` via Homebrew if it's not already on `PATH`.
2. Creates `.venv` (if missing) and activates it.
3. `pip install -r requirements.txt`, plus `pytest`, `reportlab`, `requests`,
   `openpyxl`.
4. Runs `pytest` against `services/aggregation`, `services/bbox_generator`,
   `services/chat`, `services/mcp`, `services/validation`.

`services/extraction` is skipped by default — its `list_templates()` hits
the real `bmmb_dev` Cloud SQL database at import time, so it only works with
live credentials.

### Flags

- `--with-extraction` — also runs `services/extraction`'s tests. Requires
  `GCP_PROJECT_ID`, `INSTANCE_CONNECTION_NAME`, `DB_USER`, `DB_PASS`, and
  `ADMIN_API_KEY` to already be exported in your shell (same vars as the
  GitHub Actions job for that service).

  ```bash
  export GCP_PROJECT_ID=... INSTANCE_CONNECTION_NAME=... DB_USER=... DB_PASS=... ADMIN_API_KEY=...
  scripts/dev_setup.sh --with-extraction
  ```

- `--docker` — after the tests pass, also builds and runs the unified Docker
  image:
  - Copies `.env.example` to `.env` if `.env` doesn't exist yet (never
    overwrites an existing one).
  - Backs up `.env` to `.env.bak`, then strips trailing `# comment` text from
    each line — Docker's `--env-file` passes inline comments through
    literally (unlike python-dotenv), which crashes `float()` parsing on
    values like `CONFIDENCE_THRESHOLD=0.7 # Sheet 9.4...` in chat's settings.
  - `docker build -t unified-agents .`
  - `docker run -p 8080:8080 --env-file .env unified-agents`

  ```bash
  scripts/dev_setup.sh --docker
  ```

  Before this actually works end-to-end you still need to fill in real
  values in `.env` for at least `INSTANCE_CONNECTION_NAME`, `DB_USER`,
  `DB_PASS` (extraction imports these eagerly, so even dummy strings are
  enough to let the process boot — only calls to `/extraction/extract` need
  them to resolve to something real), and run
  `gcloud auth application-default login` if you want non-stub chat/RAG or
  `/extraction/extract` to actually work.

Flags can be combined: `scripts/dev_setup.sh --with-extraction --docker`.
