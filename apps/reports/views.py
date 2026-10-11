import json
from itertools import islice
from urllib.parse import urlencode
from django.contrib import messages
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.core.permissions import require_capability
from apps.core.models import SavedQuery
from apps.analytics.jobs import online
from .models import Report, ExportJob, AIBrief, KINDS
from .forms import prepare_forms, BriefEditForm
from .tables import tables
from . import services, exports, ai
from .figures import number
from apps.core.storage import storage_root


def display_value(key,value):
    if isinstance(value,list):return '；'.join(exports.text(v) for v in value)
    if key=='status':return {'high':'高于历史阈值','low':'低于历史阈值','normal':'阈值内','insufficient':'不可判断'}.get(value,exports.text(value))
    if key=='phase':return {'validation':'参数比较','evaluation':'独立评估'}.get(value,exports.text(value))
    if key=='method' and isinstance(value,str):
        prefix,_,parameter=value.partition(':')
        return {'naive':'上一期基线','seasonal':'季节滞后','ma':'移动平均','ses':'指数平滑'}.get(prefix,value)+(f'（{parameter}）' if parameter else '')
    numeric={'value','n','supporting_crashes','injured','killed','rank','score','a','b','difference','percent','daily_a','daily_b','daily_percent','mae','rmse','step','reported','actual','predicted','absolute_error','estimate','lower','upper','observed','baseline','observed_sum','total','coverage','valid','missing','expected','conflict','excluded','ambiguous','interval_samples','reference_count'}
    return number(value) if key in numeric and value is not None else exports.text(value)


def access(view):
    return require_capability('generate_reports')(require_capability('analyze_data')(view))


@access
def home(request):
    qs=Report.objects.filter(owner=request.user).prefetch_related(Prefetch('briefs',queryset=AIBrief.objects.filter(owner=request.user),to_attr='brief_history'))
    kind=request.GET.get('kind','')
    if kind in dict(KINDS):qs=qs.filter(kind=kind)
    page=Paginator(qs,20).get_page(request.GET.get('page'))
    for report in page: report.display_brief=preferred_brief(report.brief_history)
    return render(request,'reports/home.html',{'page':page,'kind_filter':kind,
          'kinds':KINDS,'query':urlencode({'kind':kind}),'worker_online':online(),'ai_config':ai.config_status()})


@access
def new(request):
    error='';code=200
    try:main,source=prepare_forms(request.user,request.POST if request.method=='POST' else request.GET,load_saved=request.method=='GET')
    except ValidationError as e:
        return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('reports')},status=400)
    if request.method=='POST':
        try:return redirect('report_detail',pk=services.enqueue(request.user,main,source).pk)
        except ValidationError as e:error='；'.join(e.messages);code=400
    kind=main.data.get('kind','statistics')
    selected_source=None
    if kind in ('risk','forecast') and main.is_valid():
        run=main.cleaned_data.get(kind+'_run')
        if run:
            fallback_label=run.get_level_display() if kind=='risk' else run.target.get_scope_display()
            selected_source={'label':run.summary.get('object_label',fallback_label),
                             'start':run.date_from, 'end':run.date_to, 'id':str(run.pk)[:8],
                             'rule':run.rule, 'created':run.finished_at}
    return render(request,'reports/new.html',{'form':main,'source_form':source,'report_kind':kind,'error':error,
             'selected_source':selected_source,'worker_online':online()},status=code)


def preferred_brief(briefs):
    return next((b for b in briefs if b.status=='succeeded'), briefs[0] if briefs else None)


def report_context(report,request):
    context={'report':report,'worker_online':online(),'ai_config':ai.config_status(),'error_message':exports.ERRORS.get(report.error_code,exports.ERRORS['internal'])}
    if report.status=='succeeded':
        data_tables=list(tables(report))
        code=request.GET.get('table',data_tables[0].code if data_tables else '')
        table=next((t for t in data_tables if t.code==code),None)
        if data_tables and not table:raise Http404
        preview=[]
        print_fields={'statistics':['label','value','unit','n','complete'],
          'comparison':['label','a','b','difference','percent','reasons'],
          'risk_results':['label','rank','score','grade','n','limitation'],
          'history':['start','value','reported','n','reason'],
          'forecast_points':['step','period_start','estimate','lower','upper','interval_samples'],
          'backtest':['method','origin_date','period_start','step','actual','predicted','absolute_error'],
          'anomalies':['period_start','observed','baseline','lower','upper','status'],
          'flow':['label','observed_sum','total','valid','expected','complete']}
        for t in data_tables:
            columns=[c for c in t.columns if c[0] in print_fields[t.code]] if t.code in print_fields else t.columns
            preview.append({'title':t.title,'count':t.count,'columns':columns,'rows':[[display_value(key,row.get(key)) for key,label in columns] for row in t.preview(12)]})
        brief_history=list(report.briefs.filter(owner=request.user))
        context.update(data_tables=data_tables,table=table,print_tables=preview,briefs=[b for b in brief_history if b.status=='succeeded'],
                       display_brief=preferred_brief(brief_history),
                       export_jobs=report.exportjob_set.filter(status='succeeded')[:6])
        if table:
            page=Paginator(range(table.count),30).get_page(request.GET.get('page'))
            start=(page.number-1)*30
            rows=[[display_value(key,r.get(key)) for key,label in table.columns] for r in islice(table.rows(),start,start+30)]
            context.update(page=page,table_rows=rows,query=urlencode({'table':table.code}))
    return context


@access
def detail(request,pk):
    report=get_object_or_404(Report.objects.select_related('risk_run','forecast_run'),pk=pk,owner=request.user)
    return render(request,'reports/detail.html',report_context(report,request))


@access
def status(request,kind,pk):
    models={'report':Report,'export':ExportJob,'brief':AIBrief}
    if kind not in models:raise Http404
    item=get_object_or_404(models[kind],pk=pk,owner=request.user)
    return JsonResponse({'status':item.status,'label':item.get_status_display(),'worker_online':online()})


@require_POST
@access
def repeat(request,pk):
    old=get_object_or_404(Report,pk=pk,owner=request.user)
    data={**old.conditions,'kind':old.kind,'title':old.title}
    if old.risk_run_id:data['risk_run']=str(old.risk_run_id)
    if old.forecast_run_id:data['forecast_run']=str(old.forecast_run_id)
    main,source=prepare_forms(request.user,data)
    try:return redirect('report_detail',pk=services.enqueue(request.user,main,source).pk)
    except ValidationError as e:return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('report_detail',kwargs={'pk':pk})},status=400)


@access
def export_home(request):
    return render(request,'reports/exports.html',{'page':Paginator(ExportJob.objects.filter(owner=request.user),20).get_page(request.GET.get('page')),'worker_online':online()})


@access
def export_new(request):
    values=request.POST if request.method=='POST' else request.GET
    kind=values.get('kind','crashes');data=values.copy()
    saved_id=values.get('saved_query')
    if request.method=='GET' and saved_id:
        if not str(saved_id).isdigit():raise Http404
        saved=get_object_or_404(SavedQuery,pk=saved_id,owner=request.user,is_active=True,kind__in=['crashes','vehicles','persons'])
        kind=saved.kind;data=saved.conditions
    try:form=exports.make_form(kind,data)
    except ValidationError:raise Http404
    error='';code=200
    if request.method=='POST':
        try:return redirect('export_detail',pk=exports.enqueue(request.user,kind,values.get('format','csv'),form=form).pk)
        except ValidationError as e:error='；'.join(e.messages);code=400
    return render(request,'reports/export_new.html',{'form':form,'export_kind':kind,'formats':[('csv','CSV 数据包（ZIP）'),('xlsx','Excel 工作簿')],
         'format':values.get('format','csv'),'export_kinds':[(k,v) for k,v in exports.KINDS.items() if k!='report'],'error':error,'worker_online':online()},status=code)


@access
def export_detail(request,pk):
    job=get_object_or_404(ExportJob,pk=pk,owner=request.user)
    return render(request,'reports/export_detail.html',{'job':job,'worker_online':online(),'error_message':exports.ERRORS.get(job.error_code,exports.ERRORS['internal'])})


@require_POST
@access
def export_report(request,pk):
    report=get_object_or_404(Report,pk=pk,owner=request.user,status='succeeded')
    try:return redirect('export_detail',pk=exports.enqueue(request.user,'report',request.POST.get('format','xlsx'),report=report).pk)
    except ValidationError as e:return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('report_detail',kwargs={'pk':pk})},status=400)


@require_POST
@access
def export_retry(request,pk):
    old=get_object_or_404(ExportJob,pk=pk,owner=request.user,status='failed')
    try:
        form=exports.make_form(old.kind,old.conditions) if old.kind!='report' else None
        return redirect('export_detail',pk=exports.enqueue(request.user,old.kind,old.format,form=form,report=old.report).pk)
    except ValidationError as e:return render(request,'core/action_error.html',{'error':'；'.join(e.messages),'back_url':reverse('export_detail',kwargs={'pk':pk})},status=400)


@access
def download(request,pk):
    job=get_object_or_404(ExportJob,pk=pk,owner=request.user,status='succeeded')
    path=exports.path_for(job)
    if job.relative_path!=path.relative_to(storage_root()).as_posix() or not path.is_file():
        return render(request,'core/action_error.html',{'error':'导出文件不可用，请重新生成。','back_url':reverse('export_detail',kwargs={'pk':pk})},status=410)
    return FileResponse(path.open('rb'),as_attachment=True,filename='traffic-data-'+str(job.pk)+path.suffix)


@require_POST
@access
def brief_create(request,pk):
    report=get_object_or_404(Report,pk=pk,owner=request.user,status='succeeded')
    try:
        brief,reused=ai.enqueue(request.user,report)
        if reused:messages.info(request,'已复用相同输入的已有简报，保留原生成时间及编辑版本。')
        return redirect('brief_detail',pk=brief.pk)
    except (ValidationError,ai.AIError) as e:
        error=ai.ERRORS[e.code] if isinstance(e,ai.AIError) else '；'.join(e.messages)
        return render(request,'core/action_error.html',{'error':error,'back_url':reverse('report_detail',kwargs={'pk':pk})},status=400)


@access
def brief_detail(request,pk):
    brief=get_object_or_404(AIBrief.objects.select_related('report'),pk=pk,owner=request.user)
    return render(request,'reports/brief.html',{'brief':brief,'text':brief.edited_text if brief.revision else brief.generated_text,
         'worker_online':online(),'outdated_prompt':brief.prompt_version!=ai.PROMPT_VERSION,'error_message':ai.ERRORS.get(brief.error_code,ai.ERRORS['internal'])})


@access
def brief_edit(request,pk):
    brief=get_object_or_404(AIBrief,pk=pk,owner=request.user,status='succeeded')
    form=BriefEditForm(request.POST if request.method=='POST' else None,initial={'revision':brief.revision,'text':brief.edited_text if brief.revision else brief.generated_text})
    code=200
    if request.method=='POST':
        if form.is_valid():
            try:
                ai.edit(request.user,pk,form.cleaned_data['text'],form.cleaned_data['revision'])
                messages.success(request,'编辑版本已保存，模型原文保持不变。')
                return redirect('brief_detail',pk=pk)
            except ValidationError as e:form.add_error(None,e);code=409
        else:code=400
    return render(request,'reports/brief_edit.html',{'brief':brief,'form':form},status=code)


@require_POST
@access
def brief_retry(request,pk):
    get_object_or_404(AIBrief,pk=pk,owner=request.user)
    try:
        ai.retry(request.user,pk)
        return redirect('brief_detail',pk=pk)
    except (ValidationError,ai.AIError) as e:
        return render(request,'core/action_error.html',{'error':ai.ERRORS[e.code] if isinstance(e,ai.AIError) else '；'.join(e.messages),'back_url':reverse('brief_detail',kwargs={'pk':pk})},status=400)


@access
def brief_download(request,pk):
    brief=get_object_or_404(AIBrief.objects.select_related('report'),pk=pk,owner=request.user,status='succeeded')
    current=brief.edited_text if brief.revision else brief.generated_text
    header=[brief.report.title,'模型：'+brief.model,'模型生成时间：'+str(brief.finished_at),'编辑版本：'+str(brief.revision),
            '来源数据截至：'+str(brief.report.payload['data_through']),'数据版本：'+str(brief.report.data_version.get('revision')),
            '筛选：'+json.dumps(brief.report.payload['scope'],ensure_ascii=False),
            '文字含模型解释或用户编辑，需结合报告统计和数据边界核查。','']
    response=HttpResponse('\ufeff'+'\n'.join(header)+current,content_type='text/plain; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="traffic-brief-{brief.pk}.txt"'
    return response
