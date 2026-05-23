from rest_framework import serializers
from .agent_models import AgentRun, Span


class SpanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Span
        fields = [
            "span_id",
            "parent_span_id",
            "agent_name",
            "action_type",
            "tool_name",
            "status",
            "error_type",
            "latency_ms",
            "input_data",
            "output",
            "prompt_tokens",
            "completion_tokens",
            "cost",
            "created_at",
        ]


class AgentRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRun
        fields = [
            "trace_id",
            "agent_name",
            "status",
            "started_at",
            "ended_at",
            "span_count",
            "failed_span_count",
        ]
