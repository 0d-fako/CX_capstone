"""Models, migration and config agree with doc 09 §4 without needing a database."""

from __future__ import annotations

import importlib.util
from pathlib import Path


from smia.db.models import ALL_TABLES, Base

EXPECTED_TABLES = {
    "tenants", "targets", "chat_sessions", "raw_captures", "posts", "metric_snapshots",
    "post_labels", "agent_runs", "reports", "review_feedback", "pipeline_runs",
}

TENANT_SCOPED = {
    "targets", "raw_captures", "posts", "agent_runs", "reports", "review_feedback", "pipeline_runs",
    "chat_sessions",
}


def test_eleven_tables_declared():
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert {t.__tablename__ for t in ALL_TABLES} == EXPECTED_TABLES


def test_tenant_id_on_every_tenant_scoped_table():
    for name in TENANT_SCOPED:
        assert "tenant_id" in Base.metadata.tables[name].c, name


def test_idempotency_keys_exist():
    posts = Base.metadata.tables["posts"]
    snaps = Base.metadata.tables["metric_snapshots"]
    labels = Base.metadata.tables["post_labels"]
    def uniques(t):
        return {
            tuple(c.name for c in u.columns)
            for u in t.constraints
            if u.__class__.__name__ == "UniqueConstraint"
        }

    assert ("platform", "post_id") in uniques(posts)
    assert ("post_id", "captured_on") in uniques(snaps)
    assert ("post_id", "dimension", "definition_hash") in uniques(labels)


def test_migration_creates_same_tables_as_models():
    """Render 0001 as SQL (no database) and compare CREATE TABLE names with the models."""
    from io import StringIO

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.dialects import postgresql

    path = Path(__file__).resolve().parents[1] / "src/smia/db/migrations/versions/0001_initial.py"
    spec = importlib.util.spec_from_file_location("m0001", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    buf = StringIO()
    ctx = MigrationContext.configure(
        dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": buf}
    )
    with Operations.context(ctx):
        mod.upgrade()
    sql = buf.getvalue()
    made = {
        line.split()[2].strip('"')
        for line in sql.splitlines()
        if line.upper().startswith("CREATE TABLE")
    }
    assert made == EXPECTED_TABLES


def test_thresholds_and_dimensions_load():
    from smia.settings import load_dimensions, load_thresholds

    t = load_thresholds()
    assert t["version"]
    assert t["eligibility"]["cell_min_n"] == 10
    assert t["research"]["session_credit_cap"] == 60
    d = load_dimensions()
    assert d["format"]["source"] == "rule"
