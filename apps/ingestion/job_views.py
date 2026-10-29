from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.collisions.forms import style_fields
from apps.core.permissions import require_capability
from .models import DataSource, ImportJob
from .uploads import DATASETS, ERRORS, REQUIRED, UploadError, submit_upload
from .jobs import transition_job, worker_online

SOURCE_LABELS={'crashes':'交通事故事件','vehicles':'涉事车辆','persons':'涉事人员','traffic':'采样交通流量'}

class UploadForm(forms.Form):
    source=forms.ModelChoiceField(label='官方数据来源',queryset=DataSource.objects.none(),empty_label='选择来源')
    file=forms.FileField(label='本地来源文件',help_text='UTF-8 CSV（使用 API 字段名表头）、JSON 对象数组或 JSONL；可 gzip 压缩。单文件上限 512 MiB。')
    confirm=forms.BooleanField(label='确认文件来自所选官方来源，属于原始导出数据；已核对来源字段和时间范围')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        condition=Q(pk__in=[])
        for kind,dataset in DATASETS.items(): condition|=Q(kind=kind,dataset_id=dataset)
        self.fields['source'].queryset=DataSource.objects.filter(condition,is_active=True); style_fields(self)
        self.fields['source'].widget.attrs['data-upload-source']='true'

@require_capability('view_batches')
def job_list(request):
    status=request.GET.get('status','')
    jobs=ImportJob.objects.select_related('source','created_by')
    if status in dict(ImportJob._meta.get_field('status').choices): jobs=jobs.filter(status=status)
    page=Paginator(jobs,30).get_page(request.GET.get('page'))
    return render(request,'ingestion/jobs.html',{'page_obj':page,'status':status,
        'states':ImportJob._meta.get_field('status').choices,'worker_online':worker_online(),'querystring':'status='+status})

@require_capability('maintain_data')
def job_upload(request):
    form=UploadForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and form.is_valid():
        try:
            job=submit_upload(request.user,form.cleaned_data['source'],form.cleaned_data['file'])
            messages.success(request,'文件已保留，后台校验任务已排队。校验完成后需要提交导入。'); return redirect('job_detail',pk=job.pk)
        except UploadError as exc: form.add_error('file',ERRORS[exc.code])
    from .file_formats import fields_for
    schemas={str(source.pk):{'label':source.name, 'fields':', '.join(sorted(REQUIRED[source.kind])),
                            'rows':fields_for(source.kind), 'headers_url':reverse('import_headers',args=[source.kind])}
             for source in form.fields['source'].queryset}
    return render(request,'ingestion/upload.html',{'form':form,'worker_online':worker_online(),'upload_schemas':schemas,
        'schema':[(SOURCE_LABELS.get(kind,kind),', '.join(sorted(fields))) for kind,fields in REQUIRED.items()]})


@require_capability('maintain_data')
def import_headers(request, kind):
    if kind not in REQUIRED: raise Http404
    import csv
    from io import StringIO
    from .file_formats import fields_for
    output=StringIO(newline='')
    csv.writer(output).writerow([row['key'] for row in fields_for(kind)])
    response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="{kind}-field-headers.csv"'
    return response

@require_capability('view_batches')
def job_detail(request,pk):
    job=get_object_or_404(ImportJob.objects.select_related('source','created_by','requested_by','batch'),pk=pk)
    return render(request,'ingestion/job_detail.html',{'job':job,'error':ERRORS.get(job.error_code,'处理未完成，请检查来源文件。'),
        'can_operate':request.user.has_perm('core.maintain_data') and (job.created_by_id==request.user.pk or request.user.has_perm('core.manage_users')),
        'worker_online':worker_online(),'polling':job.status in ('queued_validation','validating','queued_import','running')})

@require_capability('view_batches')
def job_status(request,pk):
    job=get_object_or_404(ImportJob,pk=pk)
    response=JsonResponse({'status':job.status,'label':job.get_status_display(),'progress_rows':job.progress_rows,
        'total_rows':job.total_rows,'error':ERRORS.get(job.error_code,'') if job.error_code else '',
        'worker_online':worker_online(),'finished':job.status not in ('queued_validation','validating','queued_import','running')})
    response['Cache-Control']='no-store'; return response

@require_POST
@require_capability('maintain_data')
def job_action(request,pk):
    get_object_or_404(ImportJob,pk=pk)
    try:
        if request.POST.get('confirm')!='on': raise ValidationError('请确认任务及文件范围。')
        transition_job(request.user,pk,request.POST.get('action'))
        messages.success(request,'任务状态已更新。'); return redirect('job_detail',pk=pk)
    except ValidationError as exc:
        return render(request,'core/action_error.html',{'error':'；'.join(exc.messages),'back_url':reverse('job_detail',args=[pk])},status=409)
