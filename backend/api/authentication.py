"""DRF authentication: hashed SDK keys + JWT (simplejwt)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework import authentication, exceptions

from .auth_models import SDKApiKey
from .auth_utils import extract_prefix, verify_sdk_key


@dataclass
class SDKKeyAuth:
    """Attached to request.auth for SDK key requests."""

    key: SDKApiKey
    scopes: List[str]
    kind: str = "sdk_key"

    def has_scope(self, scope: str) -> bool:
        return self.key.has_scope(scope)


@dataclass
class LegacyKeyAuth:
    """Env-based AGENTGUARD_API_KEY fallback (dev / migration)."""

    scopes: List[str]
    kind: str = "legacy_key"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes


class SDKKeyAuthentication(authentication.BaseAuthentication):
    """
    Authorization: Api-Key ag_xxxx_…
    Also accepts X-Api-Key header.
    """

    keyword = "Api-Key"

    def authenticate(self, request) -> Optional[Tuple[Any, Any]]:
        token = self._get_token(request)
        if not token:
            return None

        # Legacy env key (dev fallback)
        legacy = (
            os.environ.get("AGENTGUARD_API_KEY", "").strip()
            or os.environ.get("AGENTGUARD_LEGACY_API_KEY", "").strip()
        )
        if legacy and secrets_equal(token, legacy):
            return (
                AnonymousUser(),
                LegacyKeyAuth(
                    scopes=["spans:write", "agents:read", "alerts:write", "*"]
                ),
            )

        prefix = extract_prefix(token)
        if not prefix:
            # Not an ag_ key — leave to other authenticators / permissions
            if legacy:
                raise exceptions.AuthenticationFailed("Invalid Api-Key")
            return None

        try:
            row = SDKApiKey.objects.get(key_prefix=prefix, is_active=True)
        except SDKApiKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid or revoked Api-Key")

        if not verify_sdk_key(token, row.key_hash):
            raise exceptions.AuthenticationFailed("Invalid Api-Key")

        SDKApiKey.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
        return (AnonymousUser(), SDKKeyAuth(key=row, scopes=list(row.scopes or [])))

    def _get_token(self, request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith(f"{self.keyword} "):
            return auth[len(self.keyword) :].strip()
        x = request.headers.get("X-Api-Key", "").strip()
        return x or None


def secrets_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


try:
    from rest_framework_simplejwt.authentication import JWTAuthentication
except ImportError:  # pragma: no cover

    class JWTAuthentication:  # type: ignore
        """Stub when simplejwt is not installed."""

        def authenticate(self, request):
            return None
