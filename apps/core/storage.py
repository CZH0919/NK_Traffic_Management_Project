"""Separate writable data for verified restored copies; code and secrets stay local."""
from pathlib import Path
from django.conf import settings


def storage_root():
    value=getattr(settings,'TRAFFIC_STORAGE_ROOT','')
    return Path(value).resolve() if value else Path(settings.BASE_DIR).resolve()
