from django.db import OperationalError, InterfaceError
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

class DatabaseAvailabilityMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, (OperationalError, InterfaceError)):
            # Render without DB-dependent context processors or request.user.
            from django.template.loader import get_template
            from django.http import HttpResponse
            return HttpResponse(get_template('errors/unavailable.html').render({}), status=503)
        return None

class MutationOutcomeMiddleware(MiddlewareMixin):
    """Only stable rejection metadata; never record form values or credentials."""
    def process_response(self,request,response):
        match=getattr(request,'resolver_match',None)
        mutators={'entity_edit','entity_create','entity_status','entity_bulk','quality_review','quality_bulk',
            'mapping_create','mapping_edit','reference_edit','account_create','account_edit','job_action','job_upload','save_query','update_query','network_edit','match_detail','match_initialize','risk_create','risk_repeat','risk_rule_create','forecast_create','forecast_repeat','report_new','report_repeat','report_export','export_new','export_retry','brief_create','brief_edit','brief_retry'}
        if request.method=='POST' and response.status_code in (400,403,409) and match and match.url_name in mutators:
            try:
                from .audit import record_event
                user=getattr(request,'user',None)
                if user is not None and user.is_authenticated:
                    record_event(actor=user,action='operation_rejected',object_type=match.kwargs.get('kind',match.url_name),
                        object_id=match.kwargs.get('pk',''),outcome='failure',details={'reason_code':{400:'validation_failed',403:'access_denied',409:'state_changed'}[response.status_code]})
            except (OperationalError,InterfaceError): pass
        return response
