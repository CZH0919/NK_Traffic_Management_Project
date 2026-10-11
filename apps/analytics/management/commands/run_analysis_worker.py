import threading
from django.core.management.base import BaseCommand,CommandError
from django.db import connection
from apps.analytics.jobs import claim,execute,recover,heartbeat,HEARTBEAT
from apps.forecasting import jobs as forecasting
from apps.reports import jobs as reporting

class Command(BaseCommand):
    help='Run local risk, forecasting, report, export and AI briefing tasks.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        with connection.cursor() as c:
            c.execute('SELECT pg_try_advisory_lock(%s,%s)',[20261005,50])
            if not c.fetchone()[0]:raise CommandError('An analysis worker is already running')
        stop=threading.Event()
        def pulse():
            while not stop.is_set():heartbeat();stop.wait(2)
        thread=threading.Thread(target=pulse,daemon=True);thread.start()
        try:
            recover()
            forecasting.recover()
            reporting.recover()
            while True:
                pk=claim()
                if pk:execute(pk)
                forecast_pk=forecasting.claim()
                if forecast_pk:forecasting.execute(forecast_pk)
                report_work=False
                for kind in ('report','export','brief'):
                    report_pk=reporting.claim(kind)
                    if report_pk:
                        report_work=True
                        reporting.execute(kind,report_pk)
                if options['once']:break
                stop.wait(1 if pk or forecast_pk or report_work else 2)
        except KeyboardInterrupt:pass
        finally:
            stop.set();thread.join(timeout=3);HEARTBEAT.unlink(missing_ok=True)
            with connection.cursor() as c:c.execute('SELECT pg_advisory_unlock(%s,%s)',[20261005,50])
