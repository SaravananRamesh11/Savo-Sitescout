"""Conversational analyst for the BD Manager (read-only; see services/analyst.py)."""
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.personas import current_persona, require_roles
from app.services import analyst

router = APIRouter(tags=["analyst"])

RATE_LIMIT, RATE_WINDOW_S = 20, 60  # protects the free-tier LLM key; per persona, in memory
_calls: dict[str, deque] = defaultdict(deque)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1500)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[Turn] = Field(default_factory=list, max_length=6)


def _rate_limit(persona_id: str) -> None:
    now, q = time.monotonic(), _calls[persona_id]
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "You are asking too quickly. Please wait a moment and try again.")
    q.append(now)


@router.post("/analyst/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    if not body.question.strip():
        raise HTTPException(422, "Ask a question.")
    _rate_limit(persona["id"])
    out = analyst.answer(db, persona, body.question.strip(), [t.model_dump() for t in body.history])
    return {"answer": out["answer"], "sources": out["sources"], "tools_used": out["tools_used"], "mode": out["mode"]}
