from django.db import migrations

def seed(apps,schema_editor):
    apps.get_model('analytics','RuleVersion').objects.get_or_create(code='historical-intensity',version=1,defaults={
        'name':'历史事故强度','parameters':{'method':'empirical-midrank-v1','weights':{'frequency':50,'severity':35,'recent':15},
            'death_weight':5,'recent_days':90,'min_support':5,'completeness':95,'thresholds':[40,60,80],'city_min_months':6},
        'definition':'本项目描述性历史强度规则：频次、已报告伤亡负担、近期强度分别占 50%、35%、15%。经验中秩百分位仅用于同参照群体比较；非官方风险模型、非事故概率、非单位流量事故率。缺失不补零，支撑和完整性不足不分级。'})

SQL="""
CREATE FUNCTION analytics_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Analysis evidence is immutable; create a new version'; END $$;
CREATE TRIGGER analysis_rule_immutable BEFORE UPDATE OR DELETE ON analytics_ruleversion FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE TRIGGER analysis_target_immutable BEFORE UPDATE OR DELETE ON analytics_analysistarget FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE TRIGGER analysis_result_immutable BEFORE UPDATE OR DELETE ON analytics_riskresult FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE FUNCTION analytics_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='succeeded' THEN RAISE EXCEPTION 'Completed analysis runs are immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analysis_run_immutable BEFORE UPDATE OR DELETE ON analytics_riskrun FOR EACH ROW EXECUTE FUNCTION analytics_run_guard();
CREATE FUNCTION analytics_result_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected text; actual text; target_road bigint; target_junction bigint; network bigint;
BEGIN
 SELECT level,network_id INTO expected,network FROM analytics_riskrun WHERE id=NEW.run_id;
 SELECT scope,road_id,junction_id INTO actual,target_road,target_junction FROM analytics_analysistarget WHERE id=NEW.target_id;
 IF expected<>actual THEN RAISE EXCEPTION 'Risk target scope differs from run'; END IF;
 IF EXISTS(SELECT 1 FROM network_roadsegment WHERE id=target_road AND network_id IS DISTINCT FROM network)
 OR EXISTS(SELECT 1 FROM network_junction WHERE id=target_junction AND network_id IS DISTINCT FROM network) THEN
  RAISE EXCEPTION 'Risk target network differs from run';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analysis_result_scope BEFORE INSERT ON analytics_riskresult FOR EACH ROW EXECUTE FUNCTION analytics_result_scope();
"""
REVERSE="""
DROP TRIGGER analysis_result_scope ON analytics_riskresult;
DROP FUNCTION analytics_result_scope();
DROP TRIGGER analysis_run_immutable ON analytics_riskrun;
DROP FUNCTION analytics_run_guard();
DROP TRIGGER analysis_result_immutable ON analytics_riskresult;
DROP TRIGGER analysis_target_immutable ON analytics_analysistarget;
DROP TRIGGER analysis_rule_immutable ON analytics_ruleversion;
DROP FUNCTION analytics_immutable();
"""
class Migration(migrations.Migration):
    dependencies=[('analytics','0001_initial')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop),migrations.RunSQL(SQL,REVERSE)]
