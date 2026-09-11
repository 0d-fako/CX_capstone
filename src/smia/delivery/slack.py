"""Post reports and notes to Slack through the incoming webhook. Buttons come in phase 2."""

from __future__ import annotations

import re
import uuid

import httpx

from smia.settings import get_settings

CHUNK = 3500  # Slack's text limit is ~4000 chars per message


def _mrkdwn(md: str) -> str:
    """Enough markdown-to-mrkdwn to read well: headings bold, **bold** -> *bold*."""
    out = []
    for line in md.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            out.append(f"*{m.group(1).strip()}*")
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        out.append(line)
    return "\n".join(out)


def post_text(text: str, *, webhook: str | None = None) -> bool:
    url = webhook or get_settings().slack_webhook_url
    if not url:
        return False
    with httpx.Client(timeout=20) as c:
        for i in range(0, len(text), CHUNK):
            r = c.post(url, json={"text": text[i : i + CHUNK]})
            r.raise_for_status()
    return True


def post_report(kind: str, markdown: str, report_id: uuid.UUID, *, validation_summary: str = "",
                webhook: str | None = None) -> bool:
    head = (
        f"*SMIA {kind} for review* · report `{report_id}`\n"
        f"{validation_summary}\n"
        f"Review with: `smia review {report_id} approve` or `smia review {report_id} revise --notes \"...\"`\n"
        "_[D] descriptive · [I] interpretive · [G] generative — check [G] first, then [I]._\n"
    )
    return post_text(head + "\n" + _mrkdwn(markdown), webhook=webhook)
