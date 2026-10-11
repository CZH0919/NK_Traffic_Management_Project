from django.core.paginator import Paginator
from django.shortcuts import render,get_object_or_404
from .models import MaintenanceRun
from .permissions import require_capability
from .maintenance import ERRORS


@require_capability('manage_system')
def history(request):
    return render(request,'core/maintenance.html',{'page_obj':Paginator(MaintenanceRun.objects.select_related('actor'),20).get_page(request.GET.get('page'))})


@require_capability('manage_system')
def detail(request,pk):
    run=get_object_or_404(MaintenanceRun.objects.select_related('actor','source_run'),pk=pk)
    return render(request,'core/maintenance_detail.html',{'run':run,'failure':ERRORS.get(run.error_code,ERRORS['internal'])})
