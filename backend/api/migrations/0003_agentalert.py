# Generated migration for AgentAlert model

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0002_agentrun_span"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentAlert",
            fields=[
                (
                    "alert_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("alert_name", models.CharField(db_index=True, max_length=200)),
                ("severity", models.CharField(default="medium", max_length=50)),
                ("agent_name", models.CharField(blank=True, db_index=True, max_length=200)),
                ("trace_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("message", models.TextField(blank=True)),
                ("payload", models.JSONField(default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("acknowledged", models.BooleanField(default=False)),
            ],
            options={
                "ordering": ["-received_at"],
            },
        ),
    ]
