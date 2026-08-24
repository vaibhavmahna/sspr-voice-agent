import json
import logging
import os
import secrets
import string
from datetime import datetime, timezone

from graph_client import GraphClient

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit.jsonl")
logger = logging.getLogger("plixa.sspr")


def get_user(client: GraphClient, identifier: str) -> dict:
    return client.get(f"/users/{identifier}")


def get_registered_recovery_contact(client: GraphClient, user_id: str) -> dict:
    """Returns whichever real, verified recovery contact Entra already has on
    file for this user - the authenticationMethods objects behind SSPR, not
    the unverified otherMails/mobilePhone profile fields (which Microsoft is
    retiring from SSPR entirely as of Sept 2026). Empty values mean the user
    has nothing registered to verify against - a signal to escalate."""
    email_methods = client.get(f"/users/{user_id}/authentication/emailMethods").get("value", [])
    phone_methods = client.get(f"/users/{user_id}/authentication/phoneMethods").get("value", [])
    mobile = next((p["phoneNumber"] for p in phone_methods if p.get("phoneType") == "mobile"), None)
    return {
        "email": email_methods[0]["emailAddress"] if email_methods else None,
        "phone": mobile,
    }


def issue_temporary_access_pass(client: GraphClient, user_id: str, lifetime_minutes: int = 60) -> dict:
    """Issues a one-time, time-limited Temporary Access Pass - the real Entra
    mechanism for signing in without a password. The caller never receives
    this directly from us; it's only ever delivered to their already-
    registered recovery contact, and the caller reads it back to prove
    they received it there."""
    resp = client.post(
        f"/users/{user_id}/authentication/temporaryAccessPassMethods",
        {"lifetimeInMinutes": lifetime_minutes, "isUsableOnce": True},
    )
    return resp.json()


def generate_temp_password(length: int = 16) -> str:
    """Generates a random password meeting Entra's default complexity rules
    (upper, lower, digit, symbol) for a forced-reset scenario."""
    symbols = "!@#$%^&*"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in symbols for c in password)
        ):
            return password


def reset_password(client: GraphClient, user_id: str, new_password: str) -> None:
    """Sets a new password directly and forces a change at next sign-in - the
    same mechanism behind the Entra admin center's own 'Reset password' button."""
    client.patch(
        f"/users/{user_id}",
        {"passwordProfile": {"password": new_password, "forceChangePasswordNextSignIn": True}},
    )


def log_action(action: str, user_id: str, reason: str, actor: str = "plixa-sspr-voice-agent") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": user_id,
        "actor": actor,
        "reason": reason,
    }
    line = json.dumps(entry)

    # Always log via the standard logging module - Azure Functions forwards this
    # to Application Insights automatically, which is the durable audit trail in
    # the cloud (the deployed filesystem is read-only under Run-From-Package).
    logger.info("audit: %s", line)

    # Also write locally when possible, for convenience during local development.
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
