import argparse
import sys

from dotenv import load_dotenv

sys.path.insert(0, "src")

from graph_client import GraphClient
from notify import send_new_password_notification, send_tap_notification
from tools import (
    generate_temp_password,
    get_registered_recovery_contact,
    get_user,
    issue_temporary_access_pass,
    log_action,
    reset_password,
)


def run(user_identifier: str, execute: bool) -> None:
    client = GraphClient()

    user = get_user(client, user_identifier)
    user_id = user["id"]
    print(f"User: {user.get('displayName')} <{user.get('userPrincipalName')}>")

    contact = get_registered_recovery_contact(client, user_id)
    if not contact["email"] and not contact["phone"]:
        print("\nNo registered recovery email or phone on file for this user.")
        print("Nothing to verify identity against - escalate to a human.")
        return
    print(f"Registered recovery contact: {contact['email'] or contact['phone']}")

    if not execute:
        print("\nDry run only. Would issue a Temporary Access Pass, send it to the")
        print("contact above, and (once the caller confirms it) reset the password.")
        print("\nRe-run with --execute to actually perform this action.")
        return

    if not contact["email"]:
        print(f"\nOnly a phone number is on file ({contact['phone']}) - SMS delivery")
        print("isn't wired up yet, only email. Escalate to a human for now.")
        return

    tap = issue_temporary_access_pass(client, user_id)
    tap_code = tap["temporaryAccessPass"]
    print(f"\nIssued a Temporary Access Pass, valid {tap.get('lifetimeInMinutes')} minutes.")

    send_tap_notification(contact["email"], tap_code, user.get("displayName", "there"))
    print(f"Sent to registered recovery email: {contact['email']}")

    confirmed_code = input("\nEnter the code the caller read back to you: ").strip()
    if confirmed_code != tap_code:
        print("\nCode does not match what was issued. Do not proceed - escalate.")
        log_action(
            "password_reset_verification_failed",
            user_id,
            "Caller-provided code did not match the issued Temporary Access Pass.",
        )
        return

    new_password = generate_temp_password()
    reset_password(client, user_id, new_password)
    send_new_password_notification(contact["email"], new_password, user.get("displayName", "there"))
    log_action(
        "reset_password",
        user_id,
        "Identity verified via Temporary Access Pass delivered to registered recovery contact.",
    )
    print("\nPassword reset. New temporary password sent to the registered recovery")
    print("email - never spoken aloud or shown here. User must change it at next sign-in.")
    print("See logs/audit.jsonl for the full trail.")


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Plixa voice-verified password reset demo agent (deterministic core, no LLM yet)"
    )
    parser.add_argument("user", help="User's UPN or object ID in the sandbox tenant")
    parser.add_argument("--execute", action="store_true", help="Actually perform the action (default is dry-run)")
    args = parser.parse_args()

    run(args.user, args.execute)
