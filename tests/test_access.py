from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.core.cache import cache
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from apps.core.models import AuditEvent
from apps.core.permissions import ROLE_PERMISSIONS

@override_settings(ALLOWED_HOSTS=['testserver','127.0.0.1','localhost'])
class AccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users={}
        for i,role in enumerate(ROLE_PERMISSIONS):
            user=User.objects.create_user(username=f'role{i}', password='A-valid-test-password-2026')
            user.groups.add(Group.objects.get(name=role))
            cls.users[role]=user

    def setUp(self):
        cache.clear()

    def test_anonymous_redirects_to_login(self):
        response=self.client.get('/data/sources/')
        self.assertEqual(response.status_code,302)
        self.assertIn('/accounts/login/?next=',response.url)

    def test_role_capability_matrix_and_direct_requests(self):
        for role,user in self.users.items():
            self.client.force_login(user)
            for path in ['/', '/data/sources/', '/accounts/access/']:
                self.assertEqual(self.client.get(path).status_code,200,(role,path))
            self.assertEqual(self.client.get('/data/batches/').status_code,403 if role=='分析用户' else 200)
            self.assertEqual(self.client.get('/system/audit/').status_code,200 if role=='系统管理员' else 403)
            self.assertEqual(user.has_perm('core.maintain_data'),role!='分析用户')
            self.assertEqual(user.has_perm('core.manage_users'),role=='系统管理员')
            self.assertFalse(user.is_superuser)

    def test_successful_login_and_post_logout_are_audited(self):
        response=self.client.post('/accounts/login/',{'username':'role0','password':'A-valid-test-password-2026'})
        self.assertRedirects(response,'/')
        self.assertTrue(AuditEvent.objects.filter(action='login',actor=self.users['系统管理员']).exists())
        self.assertEqual(self.client.get('/accounts/logout/').status_code,405)
        self.assertRedirects(self.client.post('/accounts/logout/'),'/accounts/login/')
        self.assertTrue(AuditEvent.objects.filter(action='logout').exists())
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_login_does_not_redirect_to_external_site(self):
        response=self.client.post('/accounts/login/',{'username':'role0','password':'A-valid-test-password-2026','next':'https://example.org/steal'})
        self.assertRedirects(response,'/')

    def test_bad_login_is_clear_and_never_logs_password(self):
        response=self.client.post('/accounts/login/',{'username':'role0','password':'sensitive-test-value'})
        self.assertContains(response,'用户名或密码不正确')
        self.assertFalse(AuditEvent.objects.filter(details__icontains='sensitive-test-value').exists())
        self.assertNotContains(response,'sensitive-test-value')

    def test_csrf_protection_on_login_and_logout(self):
        strict=Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post('/accounts/login/',{'username':'role0','password':'x'}).status_code,403)
        strict.force_login(self.users['系统管理员'])
        self.assertEqual(strict.post('/accounts/logout/').status_code,403)

    def test_inactive_account_cannot_login(self):
        user=self.users['分析用户']; user.is_active=False; user.save()
        self.client.post('/accounts/login/',{'username':user.username,'password':'A-valid-test-password-2026'})
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_rate_limit(self):
        for _ in range(8):
            self.client.post('/accounts/login/',{'username':'unknown','password':'wrong'})
        response=self.client.post('/accounts/login/',{'username':'role0','password':'wrong'})
        self.assertContains(response,'五分钟后重试',status_code=429)

    def test_password_change_and_session_retention(self):
        self.client.force_login(self.users['系统管理员'])
        response=self.client.post('/accounts/password/',{'old_password':'A-valid-test-password-2026','new_password1':'Another-valid-passphrase-2026','new_password2':'Another-valid-passphrase-2026'})
        self.assertRedirects(response,'/accounts/access/')
        self.assertIn('_auth_user_id',self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action='password_changed').exists())

    def test_empty_dashboard_is_honest(self):
        self.client.force_login(self.users['分析用户'])
        response=self.client.get('/')
        self.assertContains(response,'尚无已发布的数据快照')
        self.assertContains(response,'尚无已发布数据',count=4)
        for forbidden in ['D:\\','SHA-256','MVP','S01','开发中','暂未接入']:
            self.assertNotContains(response,forbidden)

    def test_one_published_dataset_does_not_claim_others_are_zero(self):
        from django.utils import timezone
        from apps.ingestion.models import Snapshot, DataSource
        Snapshot.objects.create(source=DataSource.objects.get(code='nyc-crashes'),version='empty-test',captured_at=timezone.now(),relative_path='data/raw/test.csv',status='published')
        self.client.force_login(self.users['分析用户'])
        response=self.client.get('/')
        self.assertContains(response,'尚无已发布数据',count=3)
        self.assertEqual(response.context['metrics'][0]['value'],0)

    def test_analyst_does_not_see_other_account_names(self):
        self.client.force_login(self.users['分析用户'])
        response=self.client.get('/accounts/access/')
        self.assertNotContains(response,'role0')
        self.assertNotContains(response,'账户目录')

    def test_database_failure_returns_formal_503_without_traceback(self):
        self.client.force_login(self.users['系统管理员'])
        with patch('apps.core.views.Snapshot.objects.filter',side_effect=OperationalError('sensitive connection detail')):
            response=self.client.get('/')
        self.assertContains(response,'服务暂时无法完成请求',status_code=503)
        self.assertNotContains(response,'sensitive connection detail',status_code=503)

    def test_not_found_page_is_formal(self):
        self.client.force_login(self.users['系统管理员'])
        self.assertContains(self.client.get('/does-not-exist/'),'没有找到这个页面',status_code=404)
