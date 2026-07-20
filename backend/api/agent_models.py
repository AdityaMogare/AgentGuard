import uuid
from django.db import models


class AgentRun(models.Model):
    """
    Root execution of one agent invocation, keyed by trace_id from the SDK.
    """

    trace_id = models.UUIDField(primary_key=True, editable=False)
    agent_name = models.CharField(max_length=200, db_index=True)
    status = models.CharField(max_length=50, default="RUNNING", db_index=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    span_count = models.IntegerField(default=0)
    failed_span_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["agent_name", "status"]),
            models.Index(fields=["-started_at", "agent_name"], name="idx_run_started_agent"),
            models.Index(fields=["status", "-started_at"], name="idx_run_status_started"),
        ]

    def __str__(self):
        return f"{self.agent_name} ({str(self.trace_id)[:8]})"


class Span(models.Model):
    """
    Single span in a distributed agent trace (agent step or tool call).
    """

    span_id = models.UUIDField(primary_key=True, editable=False)
    agent_run = models.ForeignKey(
        AgentRun, on_delete=models.CASCADE, related_name="spans"
    )
    parent_span_id = models.UUIDField(null=True, blank=True, db_index=True)
    agent_name = models.CharField(max_length=200, db_index=True)
    action_type = models.CharField(max_length=50, default="observe")
    tool_name = models.CharField(max_length=200, null=True, blank=True)
    status = models.CharField(max_length=50, default="SUCCESS", db_index=True)
    error_type = models.CharField(max_length=100, null=True, blank=True)
    latency_ms = models.FloatField(default=0.0)
    input_data = models.JSONField(default=dict)
    output = models.TextField(blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    cost = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["agent_run", "created_at"]),
            models.Index(fields=["status", "agent_name"]),
            models.Index(fields=["-created_at", "status"], name="idx_span_created_status"),
            models.Index(fields=["agent_name", "-created_at"], name="idx_span_agent_created"),
        ]

    def __str__(self):
        return f"Span {str(self.span_id)[:8]} ({self.status})"


class AgentAlert(models.Model):
    """Inbound Splunk alert webhook payload."""

    alert_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert_name = models.CharField(max_length=200, db_index=True)
    severity = models.CharField(max_length=50, default="medium")
    agent_name = models.CharField(max_length=200, blank=True, db_index=True)
    trace_id = models.UUIDField(null=True, blank=True, db_index=True)
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.alert_name} ({self.severity})"


class AgentMetricRollup(models.Model):
    """Hourly aggregates to avoid full table scans on dashboards."""

    agent_name = models.CharField(max_length=200, db_index=True)
    hour = models.DateTimeField(db_index=True)
    span_count = models.IntegerField(default=0)
    failure_count = models.IntegerField(default=0)
    avg_latency_ms = models.FloatField(default=0.0)
    total_cost = models.FloatField(default=0.0)

    class Meta:
        unique_together = [("agent_name", "hour")]
        ordering = ["-hour"]

    def __str__(self):
        return f"{self.agent_name}@{self.hour.isoformat()}"
