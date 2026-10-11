import json
import secrets
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from apps.core.permissions import sync_roles
from apps.core.audit import record_event

class Command(BaseCommand):
    help = 'Create three local role accounts; never reset existing credentials.'

    def add_arguments(self, parser):
        parser.add_argument('--credentials-file', required=True)

    def handle(self, *args, **options):
        path = Path(options['credentials_file']).resolve()
        if path.exists():
            raise CommandError('Credentials file already exists. Existing accounts were not changed.')
        definitions = [('traffic_admin','系统管理员'), ('traffic_editor','数据维护人员'), ('traffic_analyst','分析用户')]
        if get_user_model().objects.filter(username__in=[x[0] for x in definitions]).exists():
            raise CommandError('One or more accounts already exist. Use explicit account maintenance instead.')
        path.parent.mkdir(parents=True, exist_ok=True)
        credentials = []
        try:
            with transaction.atomic():
                sync_roles()
                for username, role in definitions:
                    password = secrets.token_urlsafe(20)
                    user = get_user_model().objects.create_user(username=username, password=password, first_name=role)
                    user.groups.add(Group.objects.get(name=role))
                    record_event(actor=user, action='account_created', object_type='account', object_id=user.pk, details={'role':role})
                    credentials.append({'username':username, 'role':role, 'password':password})
                # Exclusive creation prevents overwriting another local credentials file.
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(credentials, stream, ensure_ascii=False, indent=2)
        except Exception:
            # Do not report passwords, hashes, or connection strings in errors.
            raise CommandError('Account initialization failed; inspect local database and file access.') from None
        self.stdout.write(self.style.SUCCESS('Three role accounts created. Credentials saved to the specified private file.'))
