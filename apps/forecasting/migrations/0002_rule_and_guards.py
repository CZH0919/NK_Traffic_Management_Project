from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('analytics', 'RuleVersion').objects.get_or_create(code='count-forecast', version=1, defaults={
        'name': '周期计数预测与历史异常',
        'parameters': {
            'engine': 'count-forecast-v1', 'season': {'month': 12, 'week': 52},
            'ma_windows': {'month': [3, 6, 12], 'week': [4, 8, 13]},
            'alphas': [0.1, 0.3, 0.5, 0.7, 0.9], 'min_seasons': 3,
            'min_total': 100, 'min_nonzero_fraction': 0.5,
            'interval_quantile': 0.8, 'interval_min_samples': 10,
            'anomaly_window': 12, 'anomaly_min_total': 30,
            'anomaly_mad_scale': 1.4826, 'anomaly_multiplier': 3, 'anomaly_min_margin': 5,
        },
        'definition': '完整当地月／周计数；末尾两季节分别作参数比较与独立滚动评估；参数在评估前固定。缺失不补零，样本不足不外推。80% 绝对误差经验参考带不是未来概率保证；历史异常使用此前完整窗口的稳健阈值，不作因果判断或实时预警。',
    })


SQL = """
CREATE TRIGGER forecast_run_immutable BEFORE UPDATE OR DELETE ON forecasting_forecastrun
FOR EACH ROW EXECUTE FUNCTION analytics_run_guard();
CREATE TRIGGER forecast_point_immutable BEFORE UPDATE OR DELETE ON forecasting_forecastpoint
FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE TRIGGER forecast_fold_immutable BEFORE UPDATE OR DELETE ON forecasting_backtestfold
FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE TRIGGER forecast_anomaly_immutable BEFORE UPDATE OR DELETE ON forecasting_anomalyresult
FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE FUNCTION forecasting_run_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text; rule_code text; road bigint; junction bigint;
BEGIN
 SELECT code INTO rule_code FROM analytics_ruleversion WHERE id=NEW.rule_id;
 IF rule_code<>'count-forecast' THEN RAISE EXCEPTION 'Not a forecasting rule'; END IF;
 SELECT scope,road_id,junction_id INTO kind,road,junction FROM analytics_analysistarget WHERE id=NEW.target_id;
 IF EXISTS(SELECT 1 FROM network_roadsegment WHERE id=road AND network_id IS DISTINCT FROM NEW.network_id)
 OR EXISTS(SELECT 1 FROM network_junction WHERE id=junction AND network_id IS DISTINCT FROM NEW.network_id)
 THEN RAISE EXCEPTION 'Forecast target network differs from run'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_run_scope BEFORE INSERT OR UPDATE ON forecasting_forecastrun
FOR EACH ROW EXECUTE FUNCTION forecasting_run_scope();
CREATE FUNCTION forecasting_child_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE state text; trained date; cutoff date; horizon integer;
BEGIN
 SELECT status,training_end,date_to,r.horizon INTO state,trained,cutoff,horizon FROM forecasting_forecastrun r WHERE id=NEW.run_id;
 IF state<>'running' THEN RAISE EXCEPTION 'Only running tasks may add forecasting evidence'; END IF;
 IF TG_TABLE_NAME='forecasting_forecastpoint' THEN
  IF trained IS NULL OR NEW.period_start<=trained OR NEW.step>horizon THEN RAISE EXCEPTION 'Invalid forecast horizon'; END IF;
 ELSIF TG_TABLE_NAME='forecasting_backtestfold' THEN
  IF trained IS NULL OR NEW.period_end>trained OR NEW.step>horizon THEN RAISE EXCEPTION 'Backtest exceeds observed data'; END IF;
 ELSIF NEW.period_start>cutoff THEN RAISE EXCEPTION 'Anomaly is not in historical scope';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_point_guard BEFORE INSERT ON forecasting_forecastpoint FOR EACH ROW EXECUTE FUNCTION forecasting_child_guard();
CREATE TRIGGER forecast_fold_guard BEFORE INSERT ON forecasting_backtestfold FOR EACH ROW EXECUTE FUNCTION forecasting_child_guard();
CREATE TRIGGER forecast_anomaly_guard BEFORE INSERT ON forecasting_anomalyresult FOR EACH ROW EXECUTE FUNCTION forecasting_child_guard();
"""
REVERSE = """
DROP TRIGGER forecast_anomaly_guard ON forecasting_anomalyresult;
DROP TRIGGER forecast_fold_guard ON forecasting_backtestfold;
DROP TRIGGER forecast_point_guard ON forecasting_forecastpoint;
DROP FUNCTION forecasting_child_guard();
DROP TRIGGER forecast_run_scope ON forecasting_forecastrun;
DROP FUNCTION forecasting_run_scope();
DROP TRIGGER forecast_anomaly_immutable ON forecasting_anomalyresult;
DROP TRIGGER forecast_fold_immutable ON forecasting_backtestfold;
DROP TRIGGER forecast_point_immutable ON forecasting_forecastpoint;
DROP TRIGGER forecast_run_immutable ON forecasting_forecastrun;
"""


class Migration(migrations.Migration):
    dependencies = [('forecasting', '0001_initial'), ('analytics', '0002_rules_and_evidence')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop), migrations.RunSQL(SQL, REVERSE)]
