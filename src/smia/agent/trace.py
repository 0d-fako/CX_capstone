"""The run trace: every tool call gets a ref (T1, T2, ...) the model cites as evidence and
the validator resolves. Tools are bound to a tenant here, never by the model."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_jsonable(v) for v in o]
    if isinstance(o, uuid.UUID | datetime):
        return str(o)
    if hasattr(o, "as_dict"):
        return _jsonable(o.as_dict())
    if hasattr(o, "__dict__") and not isinstance(o, type):
        return _jsonable(vars(o))
    return o


@dataclass
class ToolCall:
    ref: str
    name: str
    input: dict[str, Any]
    output: Any
    at: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref, "name": self.name, "input": self.input,
            "output": self.output, "at": self.at, "error": self.error,
        }


@dataclass
class RunContext:
    """Bound tenant plus the growing list of tool calls for one agent run."""

    tenant_id: uuid.UUID
    run_id: uuid.UUID
    kind: str
    calls: list[ToolCall] = field(default_factory=list)
    web_uses: int = 0

    def record(self, name: str, inp: dict[str, Any], output: Any, error: str | None = None) -> str:
        ref = f"T{len(self.calls) + 1}"
        out = _jsonable(output)
        self.calls.append(ToolCall(ref=ref, name=name, input=_jsonable(inp), output=out,
                                   at=datetime.now(UTC).isoformat(), error=error))
        payload = {"ref": ref, **(out if isinstance(out, dict) else {"result": out})}
        if error:
            payload["error"] = error
        return json.dumps(payload, ensure_ascii=False, default=str)

    def by_ref(self, ref: str) -> ToolCall | None:
        for c in self.calls:
            if c.ref == ref:
                return c
        return None

    def as_list(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.calls]
