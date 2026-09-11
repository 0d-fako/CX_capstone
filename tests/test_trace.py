"""RunContext assigns sequential refs and serialises results the model can cite."""

from __future__ import annotations

import json
import uuid

from smia.agent.trace import RunContext


def test_record_assigns_refs_and_returns_json():
    ctx = RunContext(tenant_id=uuid.uuid4(), run_id=uuid.uuid4(), kind="digest")
    s1 = ctx.record("benchmarks", {"window_days": 90}, {"accounts": [], "id": uuid.uuid4()})
    s2 = ctx.record("get_post", {"post_id": 1}, {"found": False}, error="not found")
    assert json.loads(s1)["ref"] == "T1"
    j2 = json.loads(s2)
    assert j2["ref"] == "T2" and j2["error"] == "not found"
    assert ctx.by_ref("T1").name == "benchmarks" and ctx.by_ref("T9") is None
    assert [c["ref"] for c in ctx.as_list()] == ["T1", "T2"]


def test_non_dict_output_is_wrapped():
    ctx = RunContext(tenant_id=uuid.uuid4(), run_id=uuid.uuid4(), kind="digest")
    assert json.loads(ctx.record("x", {}, [1, 2]))["result"] == [1, 2]
