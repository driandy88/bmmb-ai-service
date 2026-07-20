"""
MCP server exposing a `send_email` tool over Gmail SMTP (SSL, port 465).

Runs as a stdio MCP server -- it's spawned as a subprocess by
services.mcp.agent, which connects to it with langchain-mcp-adapters. Logs go
to stderr so they never corrupt the JSON-RPC stream on stdout.

Env: SENDER_EMAIL, and APP_PASSWORD (a Gmail *App Password*, generated with
2-Step Verification on -- not the normal account password).

Run standalone (mostly for debugging):  python -m services.mcp.servers.email_server
"""
import logging
import os
import smtplib
import sys
from email.mime.text import MIMEText

from fastmcp import FastMCP

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("email_server")

mcp = FastMCP("EmailServer")


@mcp.tool()
def send_email(to_email: str, subject: str, body: str, cc_email: str = "") -> str:
    """Send a plain-text email via Gmail SMTP.

    Args:
        to_email: main recipient address
        subject: email subject line
        body: plain-text email body
        cc_email: optional CC address(es), comma-separated (empty string for none)
    """
    sender = os.environ.get("SENDER_EMAIL", "").strip()
    app_password = os.environ.get("APP_PASSWORD", "").replace(" ", "").strip()
    if not sender or not app_password:
        return "Error: SENDER_EMAIL / APP_PASSWORD are not configured on the server."

    cc_list = [a.strip() for a in cc_email.split(",") if a.strip()] if cc_email else []
    log.info("Sending as %s -> to=%s cc=%s", sender, to_email, cc_list or "-")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(sender, app_password)
            server.sendmail(sender, [to_email, *cc_list], msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        return (
            f"Error: Gmail rejected the credentials (535) for {sender}. Regenerate the "
            f"App Password (needs 2-Step Verification) and set APP_PASSWORD. Details: {e}"
        )
    except Exception as e:  # noqa: BLE001 -- surface any SMTP/network failure to the agent verbatim
        return f"Error sending email: {e}"

    cc_note = f" (cc: {', '.join(cc_list)})" if cc_list else ""
    return f"Success: Email sent to {to_email}{cc_note}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
