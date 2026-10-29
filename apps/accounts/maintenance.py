from django import forms
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.shortcuts import get_object_or_404, redirect, render
from apps.collisions.forms import style_fields
from apps.core.permissions import ROLE_PERMISSIONS, require_capability
from apps.core.services import require_actor, audit_changes, clean_reason


def account_state(user):
    return {'username':user.username,'first_name':user.first_name,'last_name':user.last_name,
        'is_active':user.is_active,'roles':list(user.groups.order_by('name').values_list('name',flat=True))}


class AccountForm(forms.Form):
    username=forms.CharField(label='登录名',max_length=150,validators=User._meta.get_field('username').validators)
    first_name=forms.CharField(label='显示名字',max_length=150,required=False)
    last_name=forms.CharField(label='显示姓氏',max_length=150,required=False)
    role=forms.ChoiceField(label='系统角色',choices=[(x,x) for x in ROLE_PERMISSIONS])
    is_active=forms.BooleanField(label='允许登录',required=False,initial=True)
    password=forms.CharField(label='新密码',required=False,widget=forms.PasswordInput,help_text='已有账户留空则保留密码；不在记录中保存密码内容。')
    password_confirm=forms.CharField(label='确认新密码',required=False,widget=forms.PasswordInput)
    reason=forms.CharField(label='维护依据',min_length=5,max_length=1000,widget=forms.Textarea)
    version=forms.CharField(required=False,widget=forms.HiddenInput)
    def __init__(self,*args,account=None,**kwargs):
        self.account=account
        initial={'role':'分析用户'}
        if account:
            initial=account_state(account); initial['role']=initial['roles'][0] if initial['roles'] else '分析用户'
            initial['version']=signing.dumps({'id':account.pk,'state':account_state(account)},salt='account-edit')
        super().__init__(*args,initial=initial,**kwargs)
        self.fields['role'].help_text='分析用户：查询与分析；数据维护人员：另可维护和导入；管理员：另可管理账户、规则和运行维护。'
        if not account:
            self.fields['password'].required = self.fields['password_confirm'].required = True
            from django.contrib.auth.password_validation import password_validators_help_texts
            self.fields['password'].help_text = ' '.join(password_validators_help_texts())
        else:
            self.fields['password'].help_text='留空保留当前密码；填写时须同时确认新密码。'
        style_fields(self)
    def clean(self):
        data=super().clean(); password=data.get('password','')
        if not self.account and not password: self.add_error('password','新增账户必须设置密码。')
        if password!=data.get('password_confirm'): self.add_error('password_confirm','两次输入的密码不一致。')
        if password:
            try: validate_password(password,User(username=data.get('username',''),first_name=data.get('first_name',''),last_name=data.get('last_name','')))
            except ValidationError as exc: self.add_error('password',exc)
        return data


@transaction.atomic
def save_account(actor, data, pk=None):
    require_actor(actor,'manage_users'); reason=clean_reason(data.get('reason'))
    with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)',[20261004,31])
    obj=User.objects.select_for_update().get(pk=pk) if pk else User()
    if obj.is_superuser: raise ValidationError('受保护的数据库初始化账户不能通过业务界面修改。')
    before=account_state(obj) if pk else None
    if pk:
        try: expected=signing.loads(data.get('version',''),salt='account-edit',max_age=86400)
        except signing.BadSignature: raise ValidationError('页面校验已失效，请重新打开。')
        if expected!={'id':obj.pk,'state':before}: raise ValidationError('账户信息已变化，请重新打开页面。')
    role=data['role']
    if role not in ROLE_PERMISSIONS: raise ValidationError('角色不受支持。')
    if User.objects.filter(username=data['username']).exclude(pk=pk).exists(): raise ValidationError('登录名已被使用。')
    if pk and obj.groups.filter(name='系统管理员').exists() and obj.is_active and (not data['is_active'] or role!='系统管理员'):
        if not User.objects.filter(is_active=True,groups__name='系统管理员').exclude(pk=pk).exists():
            raise ValidationError('必须保留至少一个可登录的系统管理员。')
    for name in ('username','first_name','last_name','is_active'): setattr(obj,name,data[name])
    if data.get('password'):
        validate_password(data['password'],obj); obj.set_password(data['password'])
    elif not pk: raise ValidationError('新增账户必须设置密码。')
    obj.full_clean(exclude=['password']); obj.save(); obj.groups.set([Group.objects.get(name=role)])
    after=account_state(obj)
    changes={f:((before or {}).get(f),after[f]) for f in after if (before or {}).get(f)!=after[f]}
    if data.get('password'): changes['password_reset']=(None,True)
    if changes: audit_changes(actor,'account_changed' if pk else 'account_created','account',obj.pk,changes,reason,data_revision=False)
    return obj


@require_capability('manage_users')
def accounts(request):
    q=request.GET.get('q','').strip()[:150]
    users=User.objects.prefetch_related('groups').order_by('username')
    if q: users=users.filter(username__icontains=q)
    return render(request,'accounts/list.html',{'page_obj':Paginator(users,40).get_page(request.GET.get('page')),'q':q,
        'querystring':__import__('urllib.parse',fromlist=['urlencode']).urlencode({'q':q})})


@require_capability('manage_users')
def account_edit(request,pk=None):
    user=get_object_or_404(User,pk=pk) if pk else None
    form=AccountForm(request.POST or None,account=user)
    if request.method=='POST' and form.is_valid():
        try:
            save_account(request.user,form.cleaned_data,pk)
            messages.success(request,'账户已保存。停用账户会失去后续请求的访问权限。'); return redirect('accounts')
        except ValidationError as exc: form.add_error(None,exc)
    return render(request,'core/maintenance_form.html',{'form':form,'title':'维护账户' if pk else '新增账户',
        'back_url':'accounts','explanation':'设置账户的访问角色和登录状态。停用账户后，原有操作记录仍可查阅。'})
