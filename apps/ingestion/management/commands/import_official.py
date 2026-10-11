from apps.core.storage import storage_root
import json
import re
import hashlib
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook
from apps.ingestion.pipeline import read_manifest,register,import_snapshot
from apps.network.models import NetworkVersion

ORDER={'boundaries':0,'nodes':1,'roads':2,'crashes':3,'vehicles':4,'persons':5,'traffic':6}

class Command(BaseCommand):
    help='Validate immutable manifests and import official rows in bounded transactions.'
    def add_arguments(self,parser):
        parser.add_argument('--label',required=True)
        parser.add_argument('--kinds',nargs='+',choices=list(ORDER),default=list(ORDER))
        parser.add_argument('--chunk-size',type=int,default=2000)
    def handle(self,*args,**options):
        label=options['label']
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',label): raise CommandError('Invalid label')
        files=list((storage_root()/'data/manifests'/label).glob('*.json'))
        if not files: raise CommandError('No manifests')
        manifests=[]
        for path in files:
            metadata=json.loads(path.read_text(encoding='utf-8'))
            if metadata.get('kind') not in options['kinds']: continue
            manifests.append(read_manifest(path))
        snapshots=[register(m) for m in sorted(manifests,key=lambda m:(ORDER[m['kind']],m.get('period_start','')))]
        by_kind={s.source.kind:s for s in snapshots}
        network=None
        if {'nodes','roads'} & set(by_kind):
            if not {'nodes','roads'}<=set(by_kind): raise CommandError('Import roads and nodes together')
            network,_=NetworkVersion.objects.get_or_create(snapshot=by_kind['roads'],defaults={
                'node_snapshot':by_kind['nodes'],'label':'LION '+by_kind['roads'].manifest['version'],
                'native_crs':'EPSG:2263','description':'Official citywide archive; node classification deferred to spatial verification.'})
        notices=set()
        if 'traffic' in by_kind:
            notice_file=storage_root()/'data/raw'/label/'references/traffic_location_errors.xlsx'
            if not notice_file.exists(): raise CommandError('Official traffic notice attachment required')
            receipt_file=notice_file.with_name(notice_file.name+'.receipt.json')
            receipt=json.loads(receipt_file.read_text(encoding='utf-8'))
            if hashlib.sha256(notice_file.read_bytes()).hexdigest()!=receipt['sha256']:
                raise CommandError('Traffic notice attachment failed integrity verification')
            workbook=load_workbook(notice_file,read_only=True,data_only=True)
            notice_rows=list(workbook.active.values)
            if str(notice_rows[1][1]).strip()!='RequestID' or str(notice_rows[1][2]).strip()!='SegmentId':
                raise CommandError('Traffic notice schema changed')
            for row in notice_rows[2:]:
                if row[1] is not None and row[2] is not None: notices.add((str(int(row[1])),str(int(row[2]))))
            workbook.close()
            for snapshot in snapshots:
                if snapshot.source.kind!='traffic': continue
                previous=snapshot.manifest.get('location_notice')
                if previous and previous['sha256']!=receipt['sha256']:
                    raise CommandError('Notice changed during import; retain and review existing classification')
                snapshot.manifest['location_notice']=receipt
                snapshot.save(update_fields=['manifest'])
        for snapshot in snapshots:
            batch,cached=import_snapshot(snapshot,network=network,notices=notices,chunk_size=options['chunk_size'])
            self.stdout.write(json.dumps({'snapshot':snapshot.pk,'kind':snapshot.source.kind,'status':batch.status,'rows':batch.checkpoint,'cached':cached}))
