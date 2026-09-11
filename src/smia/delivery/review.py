"""Review capture: the reviewer's decision is data that feeds the next brief (doc 09 §8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from smia.db.models import Report, ReviewFeedback, Tenant
from smia.db.session import db_session


def record_review(report_id: uuid.UUID, action: str, notes: str | None, reviewer: str) -> Report:
    if action not in ("approve", "revise", "user_test"):
        raise ValueError("action must be approve, revise or user_test")
    with db_session() as s:
        report = s.get(Report, report_id)
        if report is None:
            raise LookupError(f"report {report_id} not found")
        s.add(ReviewFeedback(report_id=report.id, tenant_id=report.tenant_id, reviewer=reviewer,
                             action=action, notes=notes))
        if action == "approve":
            report.status = "approved"
            report.delivered_at = datetime.now(UTC)
        elif action == "revise":
            report.status = "revised"
        return report


def activate_tenant(name: str) -> Tenant:
    with db_session() as s:
        t = s.execute(select(Tenant).where(Tenant.name == name)).scalar_one_or_none()
        if t is None:
            raise LookupError(f"no tenant named {name!r}")
        t.status = "active"
        return t
