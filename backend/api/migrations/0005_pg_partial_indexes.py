"""PostgreSQL partial index for failed/timeout spans (no-op on SQLite)."""

from django.db import migrations


PARTIAL_SQL = """
CREATE INDEX IF NOT EXISTS idx_span_failed_recent
ON api_span (created_at DESC)
WHERE status IN ('FAILED', 'TIMEOUT');
"""

REVERSE_SQL = "DROP INDEX IF EXISTS idx_span_failed_recent;"


def create_partial(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(PARTIAL_SQL)


def drop_partial(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0004_sdkapikey_indexes_rollup"),
    ]

    operations = [
        migrations.RunPython(create_partial, drop_partial),
    ]
