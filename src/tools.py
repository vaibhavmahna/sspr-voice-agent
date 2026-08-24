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


def generate_verification_code(length: int = 6) -> str:
    """Generates a random numeric one-time verification code. Deliberately
    digits-only - a real Microsoft Temporary Access Pass (mixed case,
    digits, symbols) was tried first, but live voice testing proved it
    genuinely hard to read back accurately over a call: callers correctly
    spelling out every character, including explicit "lowercase"/"uppercase"
    callouts, still failed verification because a probabilistic model
    (or the transcription itself) got a character or the phrasing of a
    symbol wrong. The security property that actually matters - only
    someone with access to the already-registered recovery contact can
    ever produce the right code - holds identically for a self-generated
    numeric code delivered the same way, without asking a caller to
    convey case or symbols by voice at all."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


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
