"""Pydantic request/response models for the MCP service, plus the server
catalog that drives the frontend form. Keeping the field metadata server-side
means adding an MCP server (or a field) needs no frontend change -- the UI
renders whatever /mcp/servers returns."""
from typing import List, Optional

from pydantic import BaseModel, Field


# ---- Gmail send -----------------------------------------------------------

class GmailSendRequest(BaseModel):
    to: str = Field(..., description="Main recipient email address")
    subject: str = Field(..., description="Subject line, used exactly as given")
    content_about: str = Field(..., description="What the email should be about; the agent writes the body")
    cc: Optional[str] = Field(None, description="Optional comma-separated CC addresses")
    system_prompt: Optional[str] = Field(
        None,
        description="Advanced: override the agent's system prompt. When omitted, the default "
                    "(which signs off as 'BMMB MCP Email Agent') is used.",
    )


class GmailSendResponse(BaseModel):
    success: bool
    detail: str                       # verbatim tool result: "Success: ..." / "Error: ..."
    agent_message: str                # the agent's final natural-language message
    to: str
    cc: Optional[str] = None
    subject: str


# ---- server catalog (drives the UI form; extensible to more servers) ------

class McpField(BaseModel):
    name: str
    label: str
    type: str = "text"                # "text" | "email" | "textarea"
    required: bool = True
    placeholder: Optional[str] = None
    help: Optional[str] = None


class McpServerInfo(BaseModel):
    key: str                          # e.g. "gmail" -> POST /mcp/gmail/send
    name: str
    description: str
    fields: List[McpField]
    system_prompt: Optional[str] = None   # the agent's default prompt, editable via the request's
                                          # optional system_prompt override (advanced); the UI prefills
                                          # its "customize prompt" box from this.
