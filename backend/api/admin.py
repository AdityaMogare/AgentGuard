from django.contrib import admin

from .agent_models import AgentRun, Span


class SpanInline(admin.TabularInline):
    model = Span
    extra = 0
    readonly_fields = (
        "span_id",
        "parent_span_id",
        "agent_name",
        "action_type",
        "tool_name",
        "status",
        "error_type",
        "latency_ms",
        "created_at",
    )
    fields = readonly_fields + ("cost", "prompt_tokens", "completion_tokens")


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = (
        "trace_id",
        "agent_name",
        "status",
        "span_count",
        "failed_span_count",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "agent_name")
    search_fields = ("trace_id", "agent_name")
    readonly_fields = ("trace_id", "started_at")
    inlines = [SpanInline]


@admin.register(Span)
class SpanAdmin(admin.ModelAdmin):
    list_display = (
        "span_id",
        "agent_run",
        "agent_name",
        "action_type",
        "status",
        "latency_ms",
        "created_at",
    )
    list_filter = ("status", "action_type", "agent_name")
    search_fields = ("span_id", "agent_name", "tool_name")
