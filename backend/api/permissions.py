import os

from rest_framework.permissions import BasePermission


class AgentGuardAPIKeyPermission(BasePermission):
    """
    Require Api-Key header when AGENTGUARD_API_KEY is set in the environment.
    When unset, ingest is open (local dev only).
    """

    message = "Valid Api-Key required."

    def has_permission(self, request, view):
        expected = os.environ.get("AGENTGUARD_API_KEY", "").strip()
        if not expected:
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Api-Key "):
            return auth[7:].strip() == expected
        return request.headers.get("X-Api-Key", "").strip() == expected
