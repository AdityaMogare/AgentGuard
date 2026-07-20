"""Permission classes for JWT users and scoped SDK keys."""
from __future__ import annotations

import os

from django.conf import settings
from rest_framework.permissions import BasePermission

from .authentication import LegacyKeyAuth, SDKKeyAuth
from .auth_models import SDKApiKey


def _auth_open() -> bool:
    """
    Open ingest/read when no keys are configured (local demo).
    Production (DEBUG=False) always requires credentials unless explicitly opened.
    """
    env_key = (
        os.environ.get("AGENTGUARD_API_KEY", "").strip()
        or os.environ.get("AGENTGUARD_LEGACY_API_KEY", "").strip()
    )
    if env_key:
        return False
    if not settings.DEBUG:
        # Prod with no env key: still allow if zero DB keys (bootstrap), else require
        return not SDKApiKey.objects.filter(is_active=True).exists()
    return not SDKApiKey.objects.filter(is_active=True).exists()


class HasScope(BasePermission):
    """Require a scope on SDK/legacy auth, or an authenticated JWT user."""

    scope = ""
    message = "Missing required scope."

    def has_permission(self, request, view):
        scope = getattr(view, "required_scope", None) or self.scope
        auth = getattr(request, "auth", None)

        if isinstance(auth, (SDKKeyAuth, LegacyKeyAuth)):
            return auth.has_scope(scope) if scope else True

        if request.user and request.user.is_authenticated:
            return True  # JWT operators have full access

        if _auth_open():
            return True

        return False


class HasScopeSpansWrite(HasScope):
    scope = "spans:write"
    message = "Valid Api-Key with spans:write required."


class HasScopeAgentsRead(HasScope):
    scope = "agents:read"
    message = "Valid credentials with agents:read required."


class HasScopeAlertsWrite(HasScope):
    scope = "alerts:write"
    message = "Valid Api-Key with alerts:write required."


class IsJWTUser(BasePermission):
    """Dashboard / admin endpoints — JWT only."""

    message = "JWT authentication required."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


# Backward-compatible alias used by older views/tests
class AgentGuardAPIKeyPermission(BasePermission):
    """
    Legacy permission: env AGENTGUARD_API_KEY compare, OR open when unset.
    Prefer HasScope* + SDKKeyAuthentication for new code.
    """

    message = "Valid Api-Key required."

    def has_permission(self, request, view):
        auth = getattr(request, "auth", None)
        if isinstance(auth, (SDKKeyAuth, LegacyKeyAuth)):
            return True

        expected = os.environ.get("AGENTGUARD_API_KEY", "").strip()
        if not expected:
            # Also accept DB SDK keys already authenticated
            if isinstance(auth, SDKKeyAuth):
                return True
            return _auth_open() or True  # historic: unset → allow

        # Env key set but authentication may not have run
        hdr = request.headers.get("Authorization", "")
        if hdr.startswith("Api-Key ") and hdr[8:].strip() == expected:
            return True
        if request.headers.get("X-Api-Key", "").strip() == expected:
            return True
        return False
