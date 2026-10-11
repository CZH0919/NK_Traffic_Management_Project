import threading
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from apps.ingestion.jobs import claim_job, execute_job, recover_interrupted, worker_heartbeat, HEARTBEAT

class Command(BaseCommand):
    help='Run the local database-backed file validation/import worker.'
    def add_arguments(self,parser): parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s,%s)',[20261004,30])
            if not cursor.fetchone()[0]: raise CommandError('A local import worker is already running.')
        stop=threading.Event()
        def heartbeat():
            while not stop.is_set(): worker_heartbeat(); stop.wait(2)
        thread=threading.Thread(target=heartbeat,daemon=True); thread.start()
        try:
            recover_interrupted()
            while True:
                pk=claim_job()
                if pk: execute_job(pk)
                if options['once']: break
                if not pk: stop.wait(2)
        except KeyboardInterrupt: pass
        finally:
            stop.set(); thread.join(timeout=3)
            HEARTBEAT.unlink(missing_ok=True)
            with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_unlock(%s,%s)',[20261004,30])
