from django.apps import AppConfig
from django.db.models.signals import post_migrate

class CoreConfig(AppConfig):
    name = 'apps.core'
    verbose_name = '公共基础'

    def ready(self):
        from .permissions import sync_roles
        post_migrate.connect(sync_roles, sender=self, dispatch_uid='traffic.sync_roles')
