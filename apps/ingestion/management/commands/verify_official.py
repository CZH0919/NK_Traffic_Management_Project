import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.ingestion.models import Snapshot
from apps.ingestion.quality import classify,quality_report,publish
from apps.ingestion.pipeline import import_lock

class Command(BaseCommand):
    help='Reconcile a completed import and optionally publish validated snapshots.'
    def add_arguments(self,parser):
        parser.add_argument('--label',required=True)
        parser.add_argument('--publish',action='store_true')
        parser.add_argument('--output',default='Project_Progress/evidence/S02/quality_report.json')
    def handle(self,*args,**options):
        with import_lock(): self.verify(options)
    def verify(self,options):
        snapshots=list(Snapshot.objects.select_related('source').filter(manifest__label=options['label']).order_by('id'))
        if not snapshots: raise CommandError('No snapshots')
        kinds={s.source.kind for s in snapshots}
        if kinds!={'crashes','vehicles','persons','traffic','roads','nodes','boundaries'}: raise CommandError('All seven source kinds required')
        expected={(f'{year}-01-01',f'{year}-12-31') for year in range(2021,2026)}
        for kind in ('crashes','vehicles','persons','traffic'):
            actual={(str(s.period_start),str(s.period_end)) for s in snapshots if s.source.kind==kind}
            if actual!=expected: raise CommandError('The complete 2021-2025 scope is required before acceptance')
        classify(snapshots)
        report=quality_report(snapshots)
        output=(Path(settings.BASE_DIR)/options['output']).resolve()
        if not output.is_relative_to(Path(settings.BASE_DIR).resolve()): raise CommandError('Report must stay in project')
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        if options['publish']: publish(snapshots,report)
        self.stdout.write(json.dumps({'checks':report['checks'],'published':options['publish'],'output':str(output.relative_to(settings.BASE_DIR))}))
