"""
FastAPI app for the MCP service.

Exposes the available MCP servers and lets the caller run one. Today: a Gmail
server that composes + sends an email via a Gemini agent (see agent.py).

To run standalone (build context is the repo root, package is `services.mcp` --
same convention as services.aggregation / services.validation):

    uvicorn services.mcp.api:app --reload

Requires SENDER_EMAIL, APP_PASSWORD (Gmail App Password) and GOOGLE_API_KEY in
the environment for /mcp/gmail/send to work; /health and /mcp/servers do not.
"""
from fastapi import APIRouter, FastAPI, HTTPException

from .agent import DEFAULT_SYSTEM_PROMPT, GmailConfigError, send_gmail
from .schemas import GmailSendRequest, GmailSendResponse, McpField, McpServerInfo

router = APIRouter(tags=["mcp"])

# The single catalog entry today. The `fields` here are exactly what the UI
# renders -- change a label / add a field here, not in the frontend.
_GMAIL = McpServerInfo(
    key="gmail",
    name="Gmail",
    description="Send an email through a Gmail MCP server. An AI agent writes the body from your description.",
    fields=[
        McpField(name="to", label="To", type="email", required=True,
                 placeholder="recipient@example.com"),
        McpField(name="cc", label="CC", type="text", required=False,
                 placeholder="a@x.com, b@y.com", help="Optional. Comma-separate multiple addresses."),
        McpField(name="subject", label="Subject", type="text", required=True,
                 placeholder="Hello from the MCP agent"),
        McpField(name="content_about", label="What should the email say?", type="textarea", required=True,
                 placeholder="e.g. Introduce yourself as the BMMB MCP agent and offer to help on the project.",
                 help="The agent writes a polite, natural body from this description."),
    ],
    system_prompt=DEFAULT_SYSTEM_PROMPT,
)

_CATALOG = {_GMAIL.key: _GMAIL}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/mcp/servers", response_model=list[McpServerInfo])
def list_servers():
    """Catalog of available MCP servers and the fields each one needs, so the UI
    can render its form dynamically. Only Gmail today."""
    return list(_CATALOG.values())


@router.post("/mcp/gmail/send", response_model=GmailSendResponse)
async def gmail_send(req: GmailSendRequest):
    """Compose + send one email via the Gmail MCP server.

    503 if the server is missing credentials; 502 if the agent/tool run fails.
    A well-formed request that Gmail rejects (bad address, auth) still returns
    200 with success=false and the reason in `detail` -- that's an outcome, not
    a transport error.
    """
    try:
        return await send_gmail(req.content_about, req.subject, req.to, req.cc, req.system_prompt)
    except GmailConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MCP agent run failed: {exc}")


# Standalone app, for `uvicorn services.mcp.api:app`. Hosts embedding this
# elsewhere should include `router` above instead.
app = FastAPI(title="MCP Service")
app.include_router(router)
