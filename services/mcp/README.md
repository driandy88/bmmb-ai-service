# MCP Service

Exposes Model Context Protocol (MCP) servers over a small HTTP API so the
frontend playground can invoke them. Today there is one server — **Gmail**: it
sends an email, with a Gemini agent writing the body from a short description.

## Endpoints

| Method & path | Auth / env | Purpose |
|---|---|---|
| `GET /health` | none | Liveness. |
| `GET /mcp/servers` | none | Catalog of available MCP servers + the form fields each needs. Drives the UI. |
| `POST /mcp/gmail/send` | needs credentials (below) | Compose + send one email via the Gmail MCP server. |

### `POST /mcp/gmail/send`

```json
// request
{ "to": "someone@example.com", "subject": "Hello", "content_about": "introduce yourself", "cc": "a@x.com, b@y.com" }
// response
{ "success": true, "detail": "Success: Email sent to someone@example.com (cc: a@x.com, b@y.com)",
  "agent_message": "…", "to": "someone@example.com", "cc": "a@x.com, b@y.com", "subject": "Hello" }
```

`cc` is optional. A well-formed request that Gmail rejects (bad address, auth)
returns **200** with `success: false` and the reason in `detail` — that's an
outcome, not a transport error. `503` means the server is missing credentials;
`502` means the agent/tool run itself failed.

## How it works

`agent.send_gmail()` spins up `servers/email_server.py` as a stdio MCP server
(subprocess), connects with `langchain-mcp-adapters`, and runs a Gemini agent
that calls the server's `send_email` tool. The email itself goes out over Gmail
SMTP (SSL, port 465).

## Environment

| Var | Needed by | Notes |
|---|---|---|
| `SENDER_EMAIL` | send | The Gmail address emails are sent from. |
| `APP_PASSWORD` | send | A Gmail **App Password** (requires 2-Step Verification) — not the account password. |
| `GOOGLE_API_KEY` | send | Gemini API key for the agent. |

## Run locally

```bash
# from the repo root (build context / package root is `services`)
pip install -r services/mcp/requirements-dev.txt
SENDER_EMAIL=... APP_PASSWORD=... GOOGLE_API_KEY=... \
  uvicorn services.mcp.api:app --reload --port 8000

# tests (hermetic — no network)
cd services/mcp && pytest tests/ -v
```

## Deployment

Same pattern as the other services (Cloud Run, path-filtered GitHub Actions —
`.github/workflows/deploy-mcp.yml`). Unlike the aggregation service, this one
needs runtime secrets, so provision once:

```bash
# service account (least privilege; only needs to read its secrets)
gcloud iam service-accounts create mcp-service-sa --display-name="MCP service"

# secrets in Secret Manager
printf '%s' "$SENDER_EMAIL"  | gcloud secrets create MCP_SENDER_EMAIL  --data-file=-
printf '%s' "$APP_PASSWORD"  | gcloud secrets create MCP_APP_PASSWORD  --data-file=-
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create MCP_GOOGLE_API_KEY --data-file=-

for s in MCP_SENDER_EMAIL MCP_APP_PASSWORD MCP_GOOGLE_API_KEY; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:mcp-service-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

The deploy workflow wires those secrets into the container as `SENDER_EMAIL` /
`APP_PASSWORD` / `GOOGLE_API_KEY` via `--set-secrets`.
