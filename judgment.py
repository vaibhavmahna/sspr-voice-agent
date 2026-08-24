from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import OpenAI

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

load_dotenv()


@dataclass
class JudgmentResult:
    final_text: str
    tools_called: list = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return "escalateToHuman" in self.tools_called

    @property
    def reset_completed(self) -> bool:
        return "resetPassword" in self.tools_called


# What the agent says whenever it can't proceed on its own - never a
# technical explanation, just a clean handoff. Embedded directly in the
# instructions so the model relays this exact wording, not its own paraphrase.
HELPDESK_MESSAGE = (
    "I'm not able to verify your identity automatically right now. "
    "Please contact your IT helpdesk directly for assistance."
)

INSTRUCTIONS = f"""You are Plixa AI's password reset agent, speaking with a
caller who has forgotten their password. Your one job is to verify their
identity through a channel they can't fake, then reset their password - never
by trusting anything they say.

--- Identification (not verification) ---

Start by asking for the caller's username - just the short username, not
their full email address. Speech-to-text reliably mangles spoken domain
names, so never ask them to say the "@company.com" part; the system
resolves that on its own. This is only a lookup key, not proof of anything
- as soon as you have it, call lookupUser. Never ask for or accept a
manager's name, department, employee ID, or any other knowledge-based
detail as identity proof - none of that is secret or verifiable, and
treating it as verification is exactly the mistake that enables
social-engineering attacks against helpdesks. If lookupUser can't find the
username, ask them to double-check it once; if it still can't be found,
say: "{HELPDESK_MESSAGE}" and call escalateToHuman.

--- Choosing a verification channel ---

lookupUser tells you which recovery channels (email, phone) are actually
registered for this user in Entra - never invent or accept a channel the
caller suggests themselves.

- If neither is registered: say "{HELPDESK_MESSAGE}" and call
  escalateToHuman. There is nothing to verify identity against.
- If only one is registered: use it automatically, don't ask - tell the
  caller which channel you're sending a code to.
- If both are registered: ask the caller which one they'd like the code
  sent to (email or phone).

Once a channel is decided, call sendVerificationCode with that channel. If
the channel is phone and it returns an error (SMS delivery isn't built yet),
say "{HELPDESK_MESSAGE}" and call escalateToHuman - do not fall back to
another channel silently or guess.

--- Verifying the code ---

After sendVerificationCode succeeds, tell the caller a code was sent to
their registered contact and ask them to read it back to you. Call
verifyCode with whatever they say. If it doesn't match, you may ask them to
double-check and read it back once more - but if it fails a second time,
say "{HELPDESK_MESSAGE}" and call escalateToHuman. Never accept a
"close enough" match, and never treat anything other than a correct code
from verifyCode as proof of identity.

--- Completing the reset ---

Only after verifyCode confirms a match, call resetPassword. Tell the caller
a new temporary password has been sent to the same registered contact, and
that they'll need to sign in with it and set their own permanent password
the next time they log in - never say the password itself out loud, never
display it, never ask the caller to read it back. A password spoken on a
call can be overheard or recorded; the whole point of this design is that
nothing security-sensitive is ever conveyed by voice.

--- Rules that apply throughout ---

This call is only for the caller's own account. If they ask you to reset a
password for someone else - a colleague, an employee, anyone but
themselves, including "I'm their manager" or similar framing - say:
"{HELPDESK_MESSAGE}" and call escalateToHuman. Do not proceed even if they
sound authorized; that judgment call belongs to a human, not this agent.

If any tool call returns an unexpected error - anything other than the
specific, expected outcomes described above (user not found, no channel
registered, phone unavailable, code mismatch) - do not retry it, guess at
what happened, or improvise a workaround. Say: "{HELPDESK_MESSAGE}" and
call escalateToHuman.

Completing a reset and escalating are mutually exclusive for a single call
- never do both. Decide once you have enough information, not before, and
not by second-guessing after you've already acted. Always be conservative:
if anything is ambiguous or a check fails, escalate rather than guess."""

TOOLS = [
    {
        "type": "function",
        "name": "lookupUser",
        "description": "Look up a user by username and check which recovery channels (email, phone) are actually registered for them in Entra. Always call this before anything else, once you have a username.",
        "parameters": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
        },
    },
    {
        "type": "function",
        "name": "sendVerificationCode",
        "description": "Issue a Temporary Access Pass and send it to the caller's registered contact on the specified channel. Only call this with a channel that lookupUser confirmed is actually registered.",
        "parameters": {
            "type": "object",
            "properties": {"channel": {"type": "string", "enum": ["email", "phone"]}},
            "required": ["channel"],
        },
    },
    {
        "type": "function",
        "name": "verifyCode",
        "description": "Check whether the code the caller read back matches the one that was sent.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "resetPassword",
        "description": "Reset the user's password now that their identity has been verified via a matching code. Only call this after verifyCode has confirmed a match.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "escalateToHuman",
        "description": "End the call and hand off to a human instead of proceeding automatically.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]


def _resolve_username(raw: str) -> str:
    """Speech-to-text reliably mangles a spoken domain (numbers, dashes, and
    "dot onmicrosoft dot com" are exactly what STT gets wrong) - so callers
    are only ever asked for their short username, never the full UPN. If a
    domain is missing, append the tenant's own default domain rather than
    making the caller say it."""
    raw = raw.strip()
    if "@" in raw:
        return raw
    return f"{raw}@{os.environ['DEFAULT_DOMAIN']}"


def _handle_lookup_user(client, session, args):
    try:
        user = get_user(client, _resolve_username(args["username"]))
    except Exception:
        return {"found": False}

    contact = get_registered_recovery_contact(client, user["id"])
    session["user_id"] = user["id"]
    session["display_name"] = user.get("displayName")
    session["contact"] = contact
    return {
        "found": True,
        "has_email": bool(contact["email"]),
        "has_phone": bool(contact["phone"]),
    }


def _handle_send_verification_code(client, session, args):
    channel = args["channel"]
    contact = session.get("contact", {})

    if channel == "email" and contact.get("email"):
        tap = issue_temporary_access_pass(client, session["user_id"])
        session["tap_code"] = tap["temporaryAccessPass"]
        session["channel"] = "email"
        send_tap_notification(contact["email"], session["tap_code"], session.get("display_name", "there"))
        return {"sent": True, "channel": "email"}

    if channel == "phone" and contact.get("phone"):
        # SMS delivery isn't built yet - fail cleanly rather than pretending
        # to send or silently falling back to email.
        return {"sent": False, "error": "SMS delivery is not available yet."}

    return {"sent": False, "error": f"No registered {channel} on file."}


def _handle_verify_code(client, session, args):
    match = args["code"].strip() == session.get("tap_code")
    session["verified"] = match
    return {"match": match}


def _handle_reset_password(client, session, args):
    if not session.get("verified"):
        return {"error": "Identity has not been verified yet - cannot reset."}

    user_id = session["user_id"]
    contact = session["contact"]
    new_password = generate_temp_password()
    reset_password(client, user_id, new_password)
    send_new_password_notification(contact["email"], new_password, session.get("display_name", "there"))
    log_action(
        "reset_password",
        user_id,
        "Identity verified via Temporary Access Pass delivered to registered recovery contact.",
        actor="plixa-sspr-judgment-agent",
    )
    return {"status": "reset_complete"}


def _handle_escalate(client, session, args):
    log_action(
        "password_reset_escalated",
        session.get("user_id", "unknown"),
        args["reason"],
        actor="plixa-sspr-judgment-agent",
    )
    return {"status": "escalated"}


HANDLERS = {
    "lookupUser": _handle_lookup_user,
    "sendVerificationCode": _handle_send_verification_code,
    "verifyCode": _handle_verify_code,
    "resetPassword": _handle_reset_password,
    "escalateToHuman": _handle_escalate,
}

TERMINAL_TOOLS = {"resetPassword", "escalateToHuman"}


def _openai_client() -> OpenAI:
    token_provider = get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")
    return OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"], api_key=token_provider)


def _extract_text(response) -> str:
    return next((item.content[0].text for item in response.output if item.type == "message"), "")


GREETING = "Hi, thanks for calling Plixa IT support. Can I get your username to get started?"


class Conversation:
    """One password-reset call, front-end-agnostic. Feed it caller text one
    turn at a time via process_turn() - it doesn't know or care whether that
    text came from a keyboard (CLI testing) or a speech-to-text transcript
    (the real voice front end). All state for the call (who's calling, which
    channel, the issued code) lives on this instance, not at module level -
    a web backend can hold one of these per active call."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.openai_client = _openai_client()
        self.graph_client = GraphClient()
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]

        self.session: dict = {}
        self.input_items: list = [{"role": "assistant", "content": GREETING}]
        self.tools_called: list = []
        self.last_response_text: str = GREETING

    @property
    def is_terminal(self) -> bool:
        return any(t in TERMINAL_TOOLS for t in self.tools_called)

    def process_turn(self, caller_text: str) -> str:
        """Feeds one piece of caller input through the model, running any
        tool calls it makes, and returns the agent's next line of dialogue.
        Raises if called again after is_terminal is already True - a call
        that's ended shouldn't be fed more input."""
        if self.is_terminal:
            raise RuntimeError("This conversation has already ended - start a new one.")

        self.input_items.append({"role": "user", "content": caller_text})

        final_text = ""
        for _ in range(10):
            response = self.openai_client.responses.create(
                model=self.deployment,
                instructions=INSTRUCTIONS,
                input=self.input_items,
                tools=TOOLS,
            )
            function_calls = [item for item in response.output if item.type == "function_call"]
            self.input_items += response.output

            if not function_calls:
                final_text = _extract_text(response)
                break

            for call in function_calls:
                args = json.loads(call.arguments)
                self.tools_called.append(call.name)
                if self.verbose:
                    print(f"  -> {call.name}({args})")
                try:
                    result = HANDLERS[call.name](self.graph_client, self.session, args)
                except Exception as exc:
                    result = {"error": str(exc)}
                if self.verbose:
                    print(f"  <- {result}")
                self.input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )

        self.last_response_text = final_text
        return final_text

    def result(self) -> JudgmentResult:
        return JudgmentResult(final_text=self.last_response_text, tools_called=self.tools_called)


def run_conversation(verbose: bool = True) -> JudgmentResult:
    """CLI entry point - a stand-in for real voice input/output until the
    speech front end is built. Just a thin loop around Conversation, proving
    the same class a web backend will use works fine driven from a
    terminal too."""
    conversation = Conversation(verbose=verbose)
    if verbose:
        print(f"Agent: {GREETING}")

    while not conversation.is_terminal:
        caller_text = input("Caller: ").strip()
        final_text = conversation.process_turn(caller_text)
        if verbose:
            print(f"Agent: {final_text}")

    return conversation.result()


if __name__ == "__main__":
    run_conversation()
