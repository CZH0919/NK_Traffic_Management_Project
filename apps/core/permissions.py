from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

COMMON = {'view_workspace', 'view_sources', 'analyze_data', 'generate_reports'}
ROLE_PERMISSIONS = {
    '系统管理员': COMMON | {'view_batches', 'maintain_data', 'manage_users', 'view_audit', 'manage_system'},
    '数据维护人员': COMMON | {'view_batches', 'maintain_data'},
    '分析用户': COMMON,
}

def sync_roles(sender=None, using='default', **kwargs):
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType
    from .models import AccessPolicy
    ct = ContentType.objects.db_manager(using).get_for_model(AccessPolicy)
    permissions = {}
    for code, label in AccessPolicy._meta.permissions:
        permissions[code], _ = Permission.objects.using(using).update_or_create(
            content_type=ct, codename=code, defaults={'name': label})
    for name, codes in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=name)
        group.permissions.set([permissions[code] for code in sorted(codes)])

def require_capability(code):
    def decorate(view):
        @login_required
        @wraps(view)
        def secured(request, *args, **kwargs):
            if not request.user.has_perm('core.' + code):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return secured
    return decorate
