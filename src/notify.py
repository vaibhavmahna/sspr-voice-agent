import os

from graph_client import GraphClient


def _mail_client() -> GraphClient:
    return GraphClient(
        tenant_id=os.environ["MAIL_TENANT_ID"],
        client_id=os.environ["MAIL_CLIENT_ID"],
        client_secret=os.environ["MAIL_CLIENT_SECRET"],
    )


def send_tap_notification(recipient_email: str, tap_code: str, display_name: str) -> None:
    """Delivers a Temporary Access Pass to the caller's already-registered
    recovery email - never spoken aloud, and never sent anywhere we haven't
    confirmed is already on file in Entra. Verification is proven by the
    caller reading this code back to us, not by anything they tell us
    up front."""
    client = _mail_client()
    mailbox = os.environ["MAIL_MAILBOX"]
    body = (
        f"Hi {display_name},\n\n"
        "Someone requested a password reset for your account. If this was you, "
        "read this one-time access code back to the agent to continue:\n\n"
        f"{tap_code}\n\n"
        "This code expires shortly and can only be used once. If you didn't "
        "request this, contact IT immediately.\n"
    )
    client.post(
        f"/users/{mailbox}/sendMail",
        {
            "message": {
                "subject": "Your password reset access code",
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient_email}}],
            }
        },
    )
