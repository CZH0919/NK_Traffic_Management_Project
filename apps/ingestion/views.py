from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render, get_object_or_404
from apps.core.permissions import require_capability
from .models import DataSource, ImportBatch, QualityIssue

@require_capability('view_sources')
def sources(request):
    sources=list(DataSource.objects.annotate(snapshot_count=Count('snapshots'), published_count=Count('snapshots',filter=Q(snapshots__status='published'))).order_by('id'))
    counts={row['snapshot__source_id']:row['n'] for row in QualityIssue.objects.filter(status='open').values('snapshot__source_id').annotate(n=Count('pk'))}
    for source in sources: source.open_issues=counts.get(source.pk,0)
    return render(request, 'ingestion/sources.html', {'sources':sources})


@require_capability('view_sources')
def source_files(request, pk):
    source=get_object_or_404(DataSource,pk=pk)
    page=Paginator(source.snapshots.order_by('-captured_at','-pk'),25).get_page(request.GET.get('page'))
    return render(request,'ingestion/source_files.html',{'source':source,'page_obj':page})

@require_capability('view_batches')
def batches(request):
    page = Paginator(ImportBatch.objects.select_related('snapshot__source'), 25).get_page(request.GET.get('page'))
    return render(request, 'ingestion/batches.html', {'page_obj':page})
