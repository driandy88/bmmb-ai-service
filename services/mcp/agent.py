"""
The Gmail MCP agent.

Spins up the stdio email MCP server (servers/email_server.py), lets a Gemini
agent compose a polite plain-text body from a short description, and sends it by
calling the server's `send_email` tool. Refactored from the mcp_testing
notebook; `send_gmail()` is the entry point the API calls.

Env: SENDER_EMAIL, APP_PASSWORD (Gmail App Password), GOOGLE_API_KEY (Gemini).
"""
import os
import sys
import warnings
from pathlib import Path
from typing import Optional, Union

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient

# Gemini's structured-tool schema doesn't use JSON Schema's additionalProperties;
# the adapter warns harmlessly about it on every call.
warnings.filterwarnings("ignore", message="Key 'additionalProperties' is not supported")

_SERVER_SCRIPT = str(Path(__file__).resolve().parent / "servers" / "email_server.py")
_MODEL = "gemini-2.5-flash"

DEFAULT_SYSTEM_PROMPT = (
    "You are an assistant that sends emails on behalf of BMMB. Write a natural, polite, "
    "well-structured plain-text body from the description you are given. Always end the email "
    "with this exact sign-off on its own lines, so the recipient can tell it came from the "
    "agent:\n\n"
    "Regards,\n"
    "BMMB MCP Email Agent\n\n"
    "Use the exact recipient, CC, and subject provided -- do not change them. Call the "
    "send_email tool exactly once. If the tool returns an error, report that error verbatim."
)


class GmailConfigError(RuntimeError):
    """The server is missing SENDER_EMAIL / APP_PASSWORD / GOOGLE_API_KEY."""


def _require_env() -> None:
    missing = [k for k in ("SENDER_EMAIL", "APP_PASSWORD", "GOOGLE_API_KEY") if not os.environ.get(k)]
    if missing:
        raise GmailConfigError(f"Missing environment variables: {', '.join(missing)}")


def normalize_cc(cc: Union[None, str, list]) -> str:
    """None / a comma-separated string / a list of addresses -> one comma-separated string ('' if empty)."""
    if not cc:
        return ""
    items = cc.split(",") if isinstance(cc, str) else cc
    return ", ".join(a.strip() for a in items if a and str(a).strip())


def _build_prompt(content_about: str, subject: str, to: str, cc_str: str) -> str:
    lines = [
        f"Send an email to {to}.",
        f"Subject (use exactly): {subject}",
        f"The email should be about: {content_about}",
        f"CC exactly these addresses (comma-separated): {cc_str}" if cc_str else "No CC.",
        "Write a polite, natural body, then send it.",
    ]
    return "\n".join(lines)


def _flatten(content) -> str:
    """Gemini messages are sometimes a list of content blocks rather than a plain string."""
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()
    return str(content)


def _tool_result(messages) -> Optional[str]:
    """The send_email tool's verbatim return ('Success: ...' / 'Error: ...'), or None if never called."""
    for m in reversed(messages):
        if getattr(m, "type", "") == "tool":
            return _flatten(m.content)
    return None


async def send_gmail(
    content_about: str,
    subject: str,
    to: str,
    cc: Union[None, str, list] = None,
    system_prompt: Optional[str] = None,
) -> dict:
    """Compose and send one email through the Gmail MCP server.

    `system_prompt` overrides how the agent writes the email (advanced use); when
    omitted, DEFAULT_SYSTEM_PROMPT is used, which signs off as "BMMB MCP Email Agent".

    Returns { success, detail, agent_message, to, cc, subject }. `detail` is the
    tool's verbatim result. Raises GmailConfigError if the server isn't configured.
    """
    _require_env()
    cc_str = normalize_cc(cc)

    client = MultiServerMCPClient({
        "email_server": {
            "command": sys.executable,
            "args": [_SERVER_SCRIPT],
            "env": dict(os.environ),
            "transport": "stdio",
        }
    })
    tools = await client.get_tools()
    agent = create_agent(
        model=ChatGoogleGenerativeAI(model=_MODEL),
        tools=tools,
        system_prompt=(system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_SYSTEM_PROMPT),
    )

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=_build_prompt(content_about, subject, to, cc_str))]}
    )
    messages = response["messages"]
    tool_out = _tool_result(messages)
    agent_message = _flatten(messages[-1].content) if messages else ""

    if tool_out is not None:
        success = tool_out.strip().startswith("Success")
        detail = tool_out
    else:
        success = False
        detail = "The agent did not call the send_email tool; nothing was sent."

    return {
        "success": success,
        "detail": detail,
        "agent_message": agent_message,
        "to": to,
        "cc": cc_str or None,
        "subject": subject,
    }
