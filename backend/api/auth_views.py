"""Auth API: JWT token issue/refresh + SDK key CRUD."""
from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth_models import SDKApiKey
from .auth_utils import generate_sdk_key
from .authentication import JWTAuthentication
from .permissions import IsJWTUser


class SDKKeyListCreateView(APIView):
    """POST creates a key (plaintext once); GET lists prefixes only."""

    authentication_classes = [c for c in [JWTAuthentication] if c]
    permission_classes = [IsAuthenticated, IsJWTUser]

    def get(self, request):
        rows = SDKApiKey.objects.all()[:100]
        return Response(
            [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "key_prefix": r.key_prefix,
                    "is_active": r.is_active,
                    "scopes": r.scopes,
                    "created_at": r.created_at.isoformat(),
                    "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                }
                for r in rows
            ]
        )

    def post(self, request):
        name = (request.data.get("name") or "sdk-key").strip()[:100]
        scopes = request.data.get("scopes") or ["spans:write", "agents:read"]
        if not isinstance(scopes, list):
            return Response({"error": "scopes must be a list"}, status=400)

        plaintext, prefix, key_hash = generate_sdk_key()
        row = SDKApiKey.objects.create(
            name=name,
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes,
        )
        return Response(
            {
                "id": str(row.id),
                "name": row.name,
                "key_prefix": row.key_prefix,
                "scopes": row.scopes,
                "api_key": plaintext,  # shown once
                "message": "Store this api_key now; it will not be shown again.",
            },
            status=status.HTTP_201_CREATED,
        )


class SDKKeyRevokeView(APIView):
    authentication_classes = [c for c in [JWTAuthentication] if c]
    permission_classes = [IsAuthenticated, IsJWTUser]

    def delete(self, request, key_id):
        try:
            row = SDKApiKey.objects.get(pk=key_id)
        except (SDKApiKey.DoesNotExist, ValueError):
            return Response({"error": "Not found"}, status=404)
        row.is_active = False
        row.save(update_fields=["is_active"])
        return Response({"message": "revoked", "id": str(row.id)})
