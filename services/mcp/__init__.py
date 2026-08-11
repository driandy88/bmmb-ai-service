"""
MCP service.

Exposes Model Context Protocol (MCP) servers over a small HTTP API so the
frontend playground can invoke them. Today there is one: a Gmail server that
sends an email, with a Gemini agent writing the body from a short description.

Module map:
  servers/email_server.py -- the MCP server itself (FastMCP, stdio transport):
                             a `send_email` tool over Gmail SMTP.
  agent.py                -- send_gmail(): spins up that server, runs the agent,
                             returns a structured result.
  schemas.py              -- pydantic request/response models + the server
                             catalog that drives the UI form.
  api.py                  -- FastAPI app/router: /health, /servers,
                             /gmail/send. (Mounted under `/mcp` in main.py's
                             unified app, so those become /mcp/health,
                             /mcp/servers, /mcp/gmail/send there.)

Adding another MCP server later is: a new servers/<x>_server.py, a runner in
agent.py, a POST /<x>/... endpoint, and one more entry in the catalog.
"""
