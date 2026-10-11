import json
from django.core.management.base import BaseCommand,CommandError
from apps.network.models import NetworkVersion
from apps.network.matching import run_matching

class Command(BaseCommand):
    help='Build derived road classifications and resumable spatial associations.'
    def add_arguments(self,parser):
        parser.add_argument('--network-id',type=int,required=True)
        parser.add_argument('--chunk-size',type=int,default=1000)
        parser.add_argument('--resume',type=str)
    def handle(self,*args,**options):
        network=NetworkVersion.objects.select_related('snapshot').get(pk=options['network_id'])
        if network.snapshot.status!='published': raise CommandError('Network snapshot must be published.')
        def progress(run): self.stdout.write(json.dumps({'run':str(run.pk),'phase':run.phase,'checkpoint':run.checkpoint}),ending='\n');self.stdout.flush()
        run=run_matching(network,chunk_size=options['chunk_size'],resume_id=options['resume'],progress=progress)
        self.stdout.write(json.dumps({'run':str(run.pk),'status':run.status,'summary':run.summary},ensure_ascii=True))
