"""Auth models for hashed SDK API keys."""
from __future__ import annotations

import uuid

from django.db import models


class SDKApiKey(models.Model):
    """
    Hashed ingest/operator key. Plaintext shown once at creation only.

    Format: ag_<prefix8>_<random32>
    Lookup by key_prefix, verify with key_hash (Django PBKDF2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=12, db_index=True, unique=True)
    key_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list)  # e.g. ["spans:write", "agents:read"]

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.key_prefix}…)"

    def has_scope(self, scope: str) -> bool:
        scopes = self.scopes or []
        return scope in scopes or "*" in scopes
