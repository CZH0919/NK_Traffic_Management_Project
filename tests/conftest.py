import pytest
from django.conf import settings
from django.db import connection
from apps.core.testing import assert_test_name

@pytest.fixture(scope='session', autouse=True)
def database_target_guard(django_db_setup, django_db_blocker):
    assert_test_name(settings.DATABASES['default']['TEST']['NAME'])
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        assert cursor.fetchone()[0] == settings.DATABASES['default']['TEST']['NAME']
