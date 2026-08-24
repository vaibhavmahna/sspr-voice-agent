import os

from graph_client import GraphClient


def _mail_client() -> GraphClient:
    return GraphClient(
        tenant_id=os.environ["MAIL_TENANT_ID"],
        client_id=os.environ["MAIL_CLIENT_ID"],
        client_secret=os.environ["MAIL_CLIENT_SECRET"],
    )


def send_verification_code_notification(recipient_email: str, code: str, display_name: str) -> None:
    """Delivers the one-time verification code to the caller's already-
    registered recovery email - never spoken aloud, and never sent anywhere
    we haven't confirmed is already on file in Entra. Verification is
    proven by the caller reading this code back to us, not by anything
    they tell us up front."""
    client = _mail_client()
    mailbox = os.environ["MAIL_MAILBOX"]
    body = (
        f"Hi {display_name},\n\n"
        "Someone requested a password reset for your account. If this was you, "
        "read this one-time verification code back to the agent to continue:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes and can only be used once. If you "
        "didn't request this, contact IT immediately.\n"
    )
    client.post(
        f"/users/{mailbox}/sendMail",
        {
            "message": {
                "subject": "Your password reset verification code",
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient_email}}],
            }
        },
    )


def send_new_password_notification(recipient_email: str, new_password: str, display_name: str) -> None:
    """Delivers the new temporary password to the same already-registered
    recovery email the Temporary Access Pass went to - never spoken aloud
    on the call itself. Reading a password out over voice would undo the
    whole point of verifying through a channel that can't be phished or
    spoofed in the first place."""
    client = _mail_client()
    mailbox = os.environ["MAIL_MAILBOX"]
    body = (
        f"Hi {display_name},\n\n"
        "Your password has been reset. Your new temporary password is:\n\n"
        f"{new_password}\n\n"
        "You'll be asked to set your own password the next time you sign in. "
        "If you didn't request this, contact IT immediately.\n"
    )
    client.post(
        f"/users/{mailbox}/sendMail",
        {
            "message": {
                "subject": "Your new temporary password",
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient_email}}],
            }
        },
    )
