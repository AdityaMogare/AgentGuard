import os
import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .agent_models import AgentRun, Span


def _span_payload(
    trace_id=None,
    span_id=None,
    agent_name="cpu_monitor",
    status="SUCCESS",
    parent_span_id=None,
):
    trace_id = trace_id or uuid.uuid4()
    span_id = span_id or uuid.uuid4()
    return {
        "span": {
            "trace_id": str(trace_id),
            "span_id": str(span_id),
            "parent_span_id": str(parent_span_id) if parent_span_id else None,
            "agent_name": agent_name,
            "action_type": "observe",
            "tool_name": "psutil.cpu_percent",
            "status": status,
            "error_type": "RuntimeError" if status == "FAILED" else None,
            "latency_ms": 12.5,
            "input": {"threshold": 90},
            "output": "ok",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0.0,
        }
    }


class SpanIngestTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_ingest_creates_run_and_span(self):
        trace_id = uuid.uuid4()
        span_id = uuid.uuid4()
        resp = self.client.post(
            "/api/v1/spans/ingest/",
            _span_payload(trace_id=trace_id, span_id=span_id),
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(AgentRun.objects.filter(trace_id=trace_id).exists())
        self.assertTrue(Span.objects.filter(span_id=span_id).exists())

    def test_ingest_missing_fields_returns_400(self):
        resp = self.client.post("/api/v1/spans/ingest/", {"span": {}}, format="json")
        self.assertEqual(resp.status_code, 400)

    @override_settings()
    def test_ingest_requires_api_key_when_set(self):
        os.environ["AGENTGUARD_API_KEY"] = "test-secret"
        try:
            resp = self.client.post(
                "/api/v1/spans/ingest/",
                _span_payload(),
                format="json",
            )
            self.assertEqual(resp.status_code, 403)

            resp = self.client.post(
                "/api/v1/spans/ingest/",
                _span_payload(),
                format="json",
                HTTP_AUTHORIZATION="Api-Key test-secret",
            )
            self.assertEqual(resp.status_code, 201)
        finally:
            os.environ.pop("AGENTGUARD_API_KEY", None)

    def test_failed_span_updates_run_status(self):
        trace_id = uuid.uuid4()
        self.client.post(
            "/api/v1/spans/ingest/",
            _span_payload(trace_id=trace_id, status="FAILED"),
            format="json",
        )
        run = AgentRun.objects.get(trace_id=trace_id)
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.failed_span_count, 1)


class AgentRunAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.trace_id = uuid.uuid4()
        self.span_id = uuid.uuid4()
        self.client.post(
            "/api/v1/spans/ingest/",
            _span_payload(trace_id=self.trace_id, span_id=self.span_id),
            format="json",
        )

    def test_list_agents(self):
        resp = self.client.get("/api/v1/agents/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 1)
        row = resp.json()[0]
        self.assertIn("analytics", row)
        self.assertIn("total_cost", row["analytics"])

    def test_retrieve_agent_with_spans(self):
        resp = self.client.get(f"/api/v1/agents/{self.trace_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["trace_id"], str(self.trace_id))
        self.assertGreaterEqual(len(data["spans"]), 1)
