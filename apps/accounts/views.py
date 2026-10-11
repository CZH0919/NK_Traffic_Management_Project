import hashlib
from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse_lazy
from apps.core.audit import record_event
from apps.core.permissions import require_capability, ROLE_PERMISSIONS
from apps.core.models import AccessPolicy
from .forms import LoginForm, AccountPasswordForm

class AccountLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def throttle_key(self):
        # Count attempts per client, regardless of the supplied account name.
        return 'login:' + hashlib.sha256(self.request.META.get('REMOTE_ADDR', '').encode()).hexdigest()

    def post(self, request, *args, **kwargs):
        if cache.get(self.throttle_key(), 0) >= 8:
            return render(request, self.template_name, {'form': self.get_form_class()(request), 'throttled': True}, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self.throttle_key()
        cache.set(key, cache.get(key, 0) + 1, timeout=300)
        record_event(action='login_failed', object_type='authentication', outcome='failure')
        return super().form_invalid(form)

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        cache.delete(self.throttle_key())
        record_event(actor=self.request.user, action='login', object_type='account', object_id=self.request.user.pk)
        return response

class AccountLogoutView(LogoutView):
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            record_event(actor=request.user, action='logout', object_type='account', object_id=request.user.pk)
        return super().post(request, *args, **kwargs)

class AccountPasswordChangeView(PasswordChangeView):
    form_class = AccountPasswordForm
    template_name = 'registration/password_change.html'
    success_url = reverse_lazy('access')

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        record_event(actor=self.request.user, action='password_changed', object_type='account', object_id=self.request.user.pk)
        messages.success(self.request, '密码已更新。')
        return response

@require_capability('view_workspace')
def access(request):
    rights = [{'label': label, 'allowed': request.user.has_perm('core.' + code)} for code, label in AccessPolicy._meta.permissions]
    users = User.objects.prefetch_related('groups').order_by('username') if request.user.has_perm('core.manage_users') else None
    return render(request, 'core/access.html', {'rights': rights, 'accounts': users, 'role_matrix': ROLE_PERMISSIONS})
