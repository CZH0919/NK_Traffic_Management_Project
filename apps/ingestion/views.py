from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import render
from apps.core.permissions import require_capability
from .models import DataSource, ImportBatch, QualityIssue

@require_capability('view_sources')
def sources(request):
    sources=list(DataSource.objects.annotate(snapshot_count=Count('snapshots')).order_by('id'))
    counts={row['snapshot__source_id']:row['n'] for row in QualityIssue.objects.filter(status='open').values('snapshot__source_id').annotate(n=Count('pk'))}
    for source in sources: source.open_issues=counts.get(source.pk,0)
    return render(request, 'ingestion/sources.html', {'sources':sources})

@require_capability('view_batches')
def batches(request):
    page = Paginator(ImportBatch.objects.select_related('snapshot__source'), 25).get_page(request.GET.get('page'))
    return render(request, 'ingestion/batches.html', {'page_obj':page})
