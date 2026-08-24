import os
import sys
import uuid

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, "src")

from judgment import GREETING, Conversation

load_dotenv()

app = FastAPI(title="Plixa SSPR Voice Agent")

# Local-only demo: the browser front end is served from a different port
# than this API during development, so CORS needs to be wide open here.
# Tighten this to a specific origin before this ever runs anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory only, by design - a call's state (who's calling, which channel,
# the issued code) only ever needs to live for the duration of that one
# call. Losing it on a server restart is correct, not a bug.
_sessions: dict[str, Conversation] = {}


class TurnRequest(BaseModel):
    text: str


@app.post("/speech-token")
def speech_token():
    """Mints a short-lived Azure Speech token so the browser never sees the
    real Speech resource key - it only ever holds a token that expires in
    minutes."""
    region = os.environ["SPEECH_REGION"]
    key = os.environ["SPEECH_KEY"]
    resp = requests.post(
        f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
        headers={"Ocp-Apim-Subscription-Key": key},
    )
    resp.raise_for_status()
    return {"token": resp.text, "region": region}


@app.post("/conversation/start")
def start_conversation():
    session_id = str(uuid.uuid4())
    _sessions[session_id] = Conversation(verbose=True)
    return {"session_id": session_id, "greeting": GREETING}


@app.post("/conversation/{session_id}/turn")
def conversation_turn(session_id: str, body: TurnRequest):
    conversation = _sessions.get(session_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unknown or expired session")
    if conversation.is_terminal:
        raise HTTPException(status_code=409, detail="This conversation has already ended")

    response_text = conversation.process_turn(body.text)

    return {
        "response": response_text,
        "is_terminal": conversation.is_terminal,
        "escalated": conversation.result().escalated if conversation.is_terminal else False,
    }
