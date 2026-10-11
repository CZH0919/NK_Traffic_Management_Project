from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

class LoginForm(AuthenticationForm):
    username = forms.CharField(label='用户名', max_length=150, widget=forms.TextInput(attrs={'class':'form-control', 'autocomplete':'username', 'autofocus': True}))
    password = forms.CharField(label='密码', strip=False, widget=forms.PasswordInput(attrs={'class':'form-control', 'autocomplete':'current-password'}))
    error_messages = {'invalid_login': '用户名或密码不正确，请重试。', 'inactive': '此账户已停用，请联系管理员。'}

class AccountPasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
