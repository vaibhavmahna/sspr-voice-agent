# SSPR Voice Agent

A voice-verified, AI-judged password reset agent for Microsoft Entra ID —
built as a companion to
[entra-automation-agent](https://github.com/vaibhavmahna/entra-automation-agent),
but kept as its own project since password reset is a distinct concern with
its own identity-verification problem to solve.

## The problem

Someone calls the helpdesk because they've forgotten their password. Over the
phone, a confident voice is the only thing being checked — the exact failure
mode behind real breaches like MGM Resorts/Caesars in 2023. Knowledge-based
questions (mother's maiden name, employee ID) are just as spoofable. The
right answer is to verify through something the caller already has, not
something they can say.

## How it verifies identity

Instead of trusting anything spoken on the call, this agent:

1. Looks up the caller's real, registered recovery contact in Entra
   (`authentication/emailMethods` / `phoneMethods` — the actual objects
   behind Microsoft's own SSPR, not the unverified `otherMails`/`mobilePhone`
   profile fields, which Microsoft is retiring from SSPR entirely as of
   September 2026).
2. Issues a real Microsoft **Temporary Access Pass** and delivers it only to
   that already-registered contact — never spoken aloud, never sent
   anywhere unverified.
3. Only resets the password once the caller reads that code back correctly.

No knowledge-based questions. Nothing the caller says is trusted until it's
proven by something only the real account owner could have received.

## Deterministic core (built so far)

- `src/tools.py` — `get_registered_recovery_contact`, `issue_temporary_access_pass`,
  `generate_temp_password`, `reset_password`, `log_action`
- `src/notify.py` — delivers the Temporary Access Pass by email via Graph
  `sendMail`
- `main_password_reset.py` — deterministic-only CLI entry point (no LLM yet),
  dry-run by default

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The app registration in `.env` needs these Microsoft Graph **Application**
permissions, with admin consent granted:

- `UserAuthMethod-Email.Read.All`
- `UserAuthMethod-Phone.Read.All`
- `UserAuthenticationMethod.ReadWrite.All`
- `User-PasswordProfile.ReadWrite.All`

Plus the **Password Administrator** directory role assigned to the app's
service principal (the least-privileged role for this — it can't reset
passwords for Helpdesk/User/Global Administrator accounts). App-only
(client-credentials) calls to the password-reset endpoint may additionally
require **User Administrator** — worth confirming against your own tenant
before assuming Password Administrator alone is enough.

A separate app registration (`MAIL_*` in `.env`) sends the notification
email, same pattern as `entra-automation-agent`'s mailbox connector —
`Mail.Send`, scoped to one mailbox via an Exchange Application Access Policy.

## Usage

```bash
python main_password_reset.py someone@yourtenant.onmicrosoft.com              # dry run
python main_password_reset.py someone@yourtenant.onmicrosoft.com --execute    # actually perform it
```

**Test against a disposable sandbox tenant first** — this genuinely issues
access passes and resets real passwords.

## Roadmap

- [ ] Wire in an LLM judgment layer (same pattern as `entra-automation-agent`'s
      `judgment.py`) — checks context, decides proceed vs. hold, instead of a
      human running the CLI by hand
- [ ] Browser-based voice front end (mic input, Azure AI Speech SDK or OpenAI
      Realtime API for STT/TTS) feeding transcribed text into the judgment
      loop — no telephony, no Azure Communication Services phone number, no
      organizational-verification bottleneck
- [ ] SMS delivery for the Temporary Access Pass as an alternative to email

## License

MIT.
