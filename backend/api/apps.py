from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import agent_models  # noqa: F401 — register AgentRun / Span with Django
