"""Local application configuration. Secrets are read only by the server."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)
TRAFFIC_STORAGE_ROOT = os.getenv('TRAFFIC_STORAGE_ROOT', '')
TRAFFIC_ISOLATED_COPY = os.getenv('TRAFFIC_ISOLATED_COPY', '') == '1'

def env_secret(name):
    value = os.getenv(name, '')
    filename = os.getenv(name + '_FILE', '')
    if filename:
        value = Path(filename).read_text(encoding='utf-8-sig').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} must be configured on the server.')
    return value

SECRET_KEY = env_secret('DJANGO_SECRET_KEY')
DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles',
    'apps.core.apps.CoreConfig', 'apps.accounts', 'apps.ingestion',
    'apps.network', 'apps.collisions', 'apps.traffic',
    'apps.analytics', 'apps.forecasting', 'apps.reports',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.DatabaseAvailabilityMiddleware',
    'apps.core.middleware.MutationOutcomeMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'apps.core.context_processors.navigation',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': os.getenv('DB_NAME', 'nk_traffic'),
    'USER': os.getenv('DB_USER', 'nk_traffic_app'),
    'PASSWORD': env_secret('DB_PASSWORD'),
    'HOST': os.getenv('DB_HOST', '127.0.0.1'),
    'PORT': os.getenv('DB_PORT', '5432'),
    'CONN_MAX_AGE': 0,
    'OPTIONS': {'connect_timeout': 5},
    'TEST': {'NAME': os.getenv('DB_TEST_NAME', 'test_nk_traffic')},
}}
BUSINESS_DATABASE_NAME = DATABASES['default']['NAME']
if not DATABASES['default']['TEST']['NAME'].startswith('test_') or DATABASES['default']['TEST']['NAME'] == BUSINESS_DATABASE_NAME:
    raise ImproperlyConfigured('The test database must have a distinct test_ name.')
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'var' / 'static'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 8 * 3600
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
TEST_RUNNER = 'apps.core.testing.IsolatedTestRunner'
AI_CONFIG_FILE = os.getenv('AI_CONFIG_FILE', '')
AI_ALLOWED_HOSTS = os.getenv('AI_ALLOWED_HOSTS', 'api.deepseek.com').split(',')
AI_DAILY_CALL_LIMIT = max(0, int(os.getenv('AI_DAILY_CALL_LIMIT', '20')))
AI_MAX_ATTEMPTS = max(1, int(os.getenv('AI_MAX_ATTEMPTS', '3')))
AI_MAX_OUTPUT_TOKENS = max(256, min(8000, int(os.getenv('AI_MAX_OUTPUT_TOKENS', '2200'))))
AI_REQUEST_TIMEOUT = max(5, min(120, int(os.getenv('AI_REQUEST_TIMEOUT', '60'))))
AI_TEMPERATURE = max(0, min(1, float(os.getenv('AI_TEMPERATURE', '0.2'))))
LOGGING = {'version': 1, 'disable_existing_loggers': False,
           'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'loggers': {'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}}}
