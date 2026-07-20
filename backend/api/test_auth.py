"""Auth tests — hashed SDK keys + JWT."""
from __future__ import annotations

import os
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .auth_models import SDKApiKey
from .auth_utils import generate_sdk_key, verify_sdk_key
from .agent_models import Span


def _span():
    return {
        "span": {
            "trace_id": str(uuid.uuid4()),
            "span_id": str(uuid.uuid4()),
            "agent_name": "cpu_monitor",
            "status": "SUCCESS",
            "latency_ms": 1.0,
        }
    }


class SDKKeyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        os.environ.pop("AGENTGUARD_API_KEY", None)

    def test_generate_stores_hash_not_plaintext(self):
        plain, prefix, hashed = generate_sdk_key()
        row = SDKApiKey.objects.create(
            name="t", key_prefix=prefix, key_hash=hashed, scopes=["spans:write"]
        )
        self.assertTrue(plain.startswith("ag_"))
        self.assertNotIn(plain, row.key_hash)
        self.assertTrue(verify_sdk_key(plain, row.key_hash))

    def test_valid_key_ingests(self):
        plain, prefix, hashed = generate_sdk_key()
        SDKApiKey.objects.create(
            name="ingest",
            key_prefix=prefix,
            key_hash=hashed,
            scopes=["spans:write", "agents:read"],
        )
        resp = self.client.post(
            "/api/v1/spans/ingest/",
            _span(),
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {plain}",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Span.objects.exists())

    def test_revoked_key_rejected(self):
        plain, prefix, hashed = generate_sdk_key()
        SDKApiKey.objects.create(
            name="revoked",
            key_prefix=prefix,
            key_hash=hashed,
            scopes=["spans:write"],
            is_active=False,
        )
        resp = self.client.post(
            "/api/v1/spans/ingest/",
            _span(),
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {plain}",
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_invalid_key_rejected_when_keys_exist(self):
        plain, prefix, hashed = generate_sdk_key()
        SDKApiKey.objects.create(
            name="real", key_prefix=prefix, key_hash=hashed, scopes=["spans:write"]
        )
        resp = self.client.post(
            "/api/v1/spans/ingest/",
            _span(),
            format="json",
            HTTP_AUTHORIZATION="Api-Key ag_deadbeef_notavalidsecret00000000",
        )
        self.assertIn(resp.status_code, (401, 403))


class JWTTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="ops", password="ops-pass-123")

    def test_jwt_can_create_and_list_keys(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        resp = self.client.post(
            "/api/v1/auth/keys/",
            {"name": "prod", "scopes": ["spans:write", "agents:read"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn("api_key", resp.data)
        self.assertTrue(resp.data["api_key"].startswith("ag_"))

        listed = self.client.get("/api/v1/auth/keys/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertNotIn("api_key", listed.data[0])
        self.assertNotIn("key_hash", listed.data[0])


@override_settings(ASYNC_SPAN_INGEST=True, CELERY_TASK_ALWAYS_EAGER=True)
class AsyncIngestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        os.environ.pop("AGENTGUARD_API_KEY", None)

    def test_async_ingest_returns_202_and_persists(self):
        payload = _span()
        resp = self.client.post("/api/v1/spans/ingest/", payload, format="json")
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(resp.data.get("async"))
        self.assertTrue(
            Span.objects.filter(span_id=payload["span"]["span_id"]).exists()
        )

    def test_duplicate_span_id_single_row(self):
        payload = _span()
        self.client.post("/api/v1/spans/ingest/", payload, format="json")
        self.client.post("/api/v1/spans/ingest/", payload, format="json")
        self.assertEqual(Span.objects.filter(span_id=payload["span"]["span_id"]).count(), 1)
