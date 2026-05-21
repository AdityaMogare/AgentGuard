import os
from celery import Celery

# Set default Django settings module for celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'promptops_backend.settings')

app = Celery('promptops_backend')

# Load task sources from all registered Django apps settings using CELERY_ namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py files in all installed apps
app.autodiscover_tasks()
