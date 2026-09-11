"""chat_sessions: the append-only record of one research conversation (doc 09 §6a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from smia.db.models import ChatSession
from smia.db.session import db_session

STAGES = ("interview", "discovering", "confirming", "collecting", "ready")


def create(user: str, channel: str = "cli") -> uuid.UUID:
    with db_session() as s:
        row = ChatSession(user=user, channel=channel, stage="interview", messages=[])
        s.add(row)
        s.flush()
        return row.id


def load(session_id: uuid.UUID) -> dict[str, Any]:
    with db_session() as s:
        row = s.get(ChatSession, session_id)
        if row is None:
            raise LookupError(f"session {session_id} not found")
        return {
            "id": row.id, "user": row.user, "channel": row.channel, "tenant_id": row.tenant_id,
            "stage": row.stage, "messages": list(row.messages or []), "credits_used": row.credits_used,
        }


def append(session_id: uuid.UUID, role: str, content: str, **meta: Any) -> None:
    with db_session() as s:
        row = s.get(ChatSession, session_id)
        msgs = list(row.messages or [])
        msgs.append({"role": role, "content": content, "at": datetime.now(UTC).isoformat(), **meta})
        row.messages = msgs
        flag_modified(row, "messages")


def set_stage(session_id: uuid.UUID, stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    with db_session() as s:
        s.get(ChatSession, session_id).stage = stage


def bind_tenant(session_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    with db_session() as s:
        s.get(ChatSession, session_id).tenant_id = tenant_id


def add_credits(session_id: uuid.UUID, n: int) -> int:
    with db_session() as s:
        row = s.get(ChatSession, session_id)
        row.credits_used = (row.credits_used or 0) + n
        return row.credits_used


def credits_used(session_id: uuid.UUID) -> int:
    with db_session() as s:
        return s.get(ChatSession, session_id).credits_used or 0
