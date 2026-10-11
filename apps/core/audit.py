from .models import AuditEvent

ACTION_LABELS = {
    'maintenance_started':'开始运行维护','maintenance_finished':'完成运行维护','maintenance_failed':'运行维护失败',
    'report_requested':'提交分析报告','report_created':'保存分析报告','report_failed':'分析报告失败',
    'export_requested':'提交数据导出','export_created':'完成数据导出','export_failed':'数据导出失败',
    'brief_requested':'提交 AI 简报','brief_created':'保存 AI 简报','brief_failed':'AI 简报失败',
    'brief_edited':'编辑 AI 简报','brief_reused':'复用已有 AI 简报',
    'forecast_requested':'提交统计预测','forecast_computed':'完成统计预测','forecast_failed':'统计预测计算失败',
    'risk_requested':'提交历史风险计算','risk_computed':'完成历史风险计算','risk_failed':'历史风险计算失败','risk_rule_created':'建立风险规则版本',
    'spatial_rebuilt':'更新空间关联','spatial_reviewed':'核查空间关联','network_changed':'维护道路或节点','site_changed':'维护采样地点',
    'operation_rejected':'维护请求未通过',
    'login': '登录系统', 'logout': '退出系统', 'login_failed': '登录失败',
    'password_changed': '修改密码', 'account_created': '创建账户',
    'data_changed': '修正数据', 'import_started': '开始导入', 'import_finished': '完成导入',
    'data_created':'新增记录','data_status_changed':'变更有效状态','quality_reviewed':'核查质量问题',
    'reference_changed':'维护基础信息','query_saved':'保存查询条件','query_deleted':'删除查询条件',
    'account_changed':'维护账户','import_submitted':'提交导入任务','import_validated':'校验导入文件',
}
ALLOWED_DETAILS = {'count', 'batch_id', 'snapshot_id', 'reason_code', 'role', 'fields', 'revision'}

def record_event(*, actor=None, action, object_type, object_id='', outcome='success', details=None):
    """Call inside the same atomic block as a successful business mutation.

    Use a separate transaction after rollback for a failure. Never supply secrets,
    request.POST, raw API responses, DSNs or arbitrary user text to this interface.
    """
    details = details or {}
    if action not in ACTION_LABELS or set(details) - ALLOWED_DETAILS:
        raise ValueError('Unsupported audit action or details.')
    if any(not isinstance(v, (str, int, bool, type(None), list)) for v in details.values()):
        raise ValueError('Audit details must be scalar values or field-name lists.')
    user = actor if actor is not None and actor.is_authenticated else None
    return AuditEvent.objects.create(actor=user, actor_label=user.username if user else '',
        action=action, object_type=object_type, object_id=str(object_id), outcome=outcome, details=details)
