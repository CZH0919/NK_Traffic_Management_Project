from .settings import *  # noqa: F403

# Even code executed before pytest-django setup can only see the isolated database.
DATABASES['default']['NAME'] = DATABASES['default']['TEST']['NAME']
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'traffic-tests'}}
