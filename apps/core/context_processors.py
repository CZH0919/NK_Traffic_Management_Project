def navigation(request):
    from django.conf import settings
    from .navigation import return_context
    match = request.resolver_match
    page = match.url_name if match else ''
    sections = [
        (('entity_', 'collision_matches'), '事故与关联档案', 'records'),
        (('network_', 'spatial_', 'match_', 'map'), '道路与地图', 'map'),
        (('traffic_',), '交通流量', 'traffic'),
        (('statistics', 'comparison', 'analysis_'), '统计与对比', 'statistics'),
        (('risk_',), '历史风险', 'risk'),
        (('forecast_',), '预测与回测', 'forecast'),
        (('report', 'brief_', 'export'), '报告与简报', 'reports'),
        (('quality_',), '数据质量', 'data'),
        (('job',), '导入任务', 'data'),
        (('reference', 'mapping_', 'sources', 'source_files', 'batches'), '数据与来源', 'data'),
        (('saved_queries', 'save_query', 'load_query'), '我的查询', 'reports'),
        (('maintenance',), '运行维护', 'maintenance'),
        (('account', 'access', 'password_', 'audit'), '系统管理', 'roles'),
        (('help',), '使用指南', 'start'),
    ]
    breadcrumb, anchor = next(((label, anchor) for prefixes,label,anchor in sections if page.startswith(prefixes)), ('工作台','start'))
    section={'entity_create':'fields','entity_edit':'fields','traffic_flow':'results','risk_detail':'results','risk_run':'results',
             'forecast_run':'results','job_upload':'files','quality_detail':'results','report_new':'steps','export_new':'fields'}.get(page,'')
    return {**return_context(request), 'breadcrumb_label': breadcrumb, 'help_anchor': anchor,
            'help_url': '/help/' if anchor == 'start' else '/help/' + anchor + '/' + ('#'+section if section else ''),
            'is_help':page in ('help','help_topic'),
            'current_page': page, 'ui_asset_version':'20261029-product3',
            'report_workspace': bool(match and match.url_name in ('reports','report_new','report_detail','report_repeat','exports','export_new','export_detail','brief_detail','brief_edit')),
            'app_name': '城市交通研究','isolated_copy':settings.TRAFFIC_ISOLATED_COPY}
