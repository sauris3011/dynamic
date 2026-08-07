"""Analyst assistant endpoints (W9).

Stateless by design: the client holds the transcript and sends back the part
that matters. A server-side conversation store would be a second place decisions
get discussed and a second thing to keep in step with the audit log — and the
durable record of what was asked already lives there.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from pricing.chat import service
from pricing.core.logging import get_logger

logger = get_logger("pricing.routes.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


class Turn(BaseModel):
    role: Literal["analyst", "assistant"]
    text: str = Field(max_length=4000)


class ChatContext(BaseModel):
    """What the analyst is looking at, so 'this one' can be resolved.

    Context is a hint about *which* record to answer from. It cannot widen what
    the assistant is allowed to see, because there is nothing it is not allowed
    to see — every read here is a read the UI could make itself.
    """

    view: str | None = Field(None, max_length=60)
    run_id: str | None = Field(None, max_length=40)
    rec_id: str | None = Field(None, max_length=40)
    sku: str | None = Field(None, max_length=40)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    history: list[Turn] = Field(default_factory=list, max_length=20)
    context: ChatContext = Field(default_factory=ChatContext)
    actor: str = Field("operator", max_length=80)


@router.post("")
def ask(payload: ChatRequest) -> dict:
    """Answer a question about the catalog, its history, a scenario, or a run.

    Always 200 with a body: an unanswerable question produces an answer saying
    so, with the reason. A chat surface that returns 4xx for "I don't have that"
    turns a normal conversational outcome into an error the operator has to
    interpret.
    """
    return service.ask(
        question=payload.message,
        history=[turn.model_dump() for turn in payload.history],
        context=payload.context.model_dump(exclude_none=True),
        actor=payload.actor,
    )


@router.get("/suggestions")
def suggestions(limit: int = Query(6, ge=1, le=12)) -> dict:
    """Opening questions, drawn from what this instance currently holds."""
    return service.suggestions(limit)
