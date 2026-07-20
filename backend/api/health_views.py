"""Health + pipeline status for ops."""
from __future__ import annotations

import os

from django.conf import settings
from django.db import connection
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Unauthenticated liveness/readiness for load balancers and compose."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        db_ok = False
        db_vendor = None
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            db_ok = True
            db_vendor = connection.vendor
        except Exception as exc:  # noqa: BLE001
            db_err = str(exc)
        else:
            db_err = None

        redis_ok = None
        queue_depth = None
        broker = getattr(settings, "CELERY_BROKER_URL", "")
        try:
            import redis

            # redis://host:6379/0
            client = redis.from_url(broker, socket_connect_timeout=1)
            redis_ok = client.ping()
            if redis_ok:
                queue_depth = client.llen("spans")
        except Exception:
            redis_ok = False

        payload = {
            "status": "ok" if db_ok else "degraded",
            "database": {"ok": db_ok, "vendor": db_vendor, "error": db_err},
            "redis": {"ok": redis_ok, "broker": broker.split("@")[-1] if broker else None},
            "queue": {"spans_depth": queue_depth},
            "async_span_ingest": bool(getattr(settings, "ASYNC_SPAN_INGEST", False)),
            "debug": settings.DEBUG,
        }
        code = 200 if db_ok else 503
        return Response(payload, status=code)
