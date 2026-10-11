from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner

def assert_test_name(name):
    if not name.startswith('test_') or name == settings.BUSINESS_DATABASE_NAME:
        raise ImproperlyConfigured('Refusing to run tests against a business database.')

class IsolatedTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        assert_test_name(settings.DATABASES['default']['TEST']['NAME'])
        if not self.keepdb:
            raise ImproperlyConfigured('Use --keepdb with the pre-provisioned test database.')
        return super().setup_databases(**kwargs)
