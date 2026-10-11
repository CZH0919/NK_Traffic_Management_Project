"""Full immutable report data tables; previews and exports share this contract."""
from dataclasses import dataclass
from itertools import islice
import json


@dataclass
class Table:
    code: str
    title: str
    columns: list
    count: int
    factory: object

    def rows(self):
        return self.factory()

    def preview(self, limit=20):
        return list(islice(self.rows(),limit))


def memory_table(code,title,columns,rows):
    return Table(code,title,columns,len(rows),lambda:iter(rows))


def tables(report):
    for item in report.payload.get('tables',[]):
        yield memory_table(item['code'],item['title'],item['columns'],item['rows'])
    if report.kind=='risk':
        qs=report.risk_run.results.order_by('rank','id')
        columns=[('label','对象'),('target_id','分析对象编号'),('rank','名次'),('score','历史强度分'),('grade','等级'),('n','支撑事故（起）'),('injured','已报告受伤（人）'),('killed','已报告死亡（人）'),('limitation','适用限制'),('components','评分构成')]
        def rows():
            for item in qs.iterator(chunk_size=2000):
                yield dict(label=item.label,target_id=item.target_id,rank=item.rank,score=item.score,grade=item.get_grade_display(),n=item.counts['n'],injured=item.counts['injured'],killed=item.counts['killed'],limitation=item.limitation,components=json.dumps(item.components,ensure_ascii=False))
        yield Table('risk_results','全部对象风险结果',columns,qs.count(),rows)
    if report.kind=='forecast':
        run=report.forecast_run
        points=run.points.order_by('step')
        yield Table('forecast_points','未来周期外推',[('step','步长'),('period_start','周期起日'),('period_end','周期止日'),('estimate','估计值'),('lower','经验下界'),('upper','经验上界'),('interval_samples','误差样本数'),('interval_method','参考带方法')],points.count(),lambda:points.values('step','period_start','period_end','estimate','lower','upper','interval_samples','interval_method').iterator(chunk_size=2000))
        folds=run.folds.order_by('phase','origin_date','step','method')
        yield Table('backtest','全部参数比较及回测',[('phase','阶段'),('method','方法参数'),('origin_date','预测原点'),('train_end','训练截止'),('period_start','目标起日'),('period_end','目标止日'),('step','步长'),('actual','实际值'),('predicted','预测值'),('baseline','上一期基线'),('absolute_error','绝对误差')],folds.count(),lambda:folds.values('phase','method','origin_date','train_end','period_start','period_end','step','actual','predicted','baseline','absolute_error').iterator(chunk_size=2000))
        anomalies=run.anomalies.order_by('period_start')
        fields=[('period_start','周期起日'),('period_end','周期止日'),('observed','已观测'),('baseline','历史中位基线'),('lower','阈值下界'),('upper','阈值上界'),('reference_from','参照起日'),('reference_to','参照止日'),('reference_count','参照期数'),('status','判断状态'),('reason','说明')]
        yield Table('anomalies','全部历史异常判断',fields,anomalies.count(),lambda:anomalies.values(*(x[0] for x in fields)).iterator(chunk_size=2000))


def table_payload(code,title,columns,rows):
    return dict(code=code,title=title,columns=columns,rows=rows)
