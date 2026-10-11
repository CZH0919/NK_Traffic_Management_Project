from django.db import migrations

SQL="""
CREATE OR REPLACE FUNCTION reports_relations() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_key bigint; state text;
BEGIN
 IF TG_TABLE_NAME='reports_reportsnapshot' THEN
  SELECT status INTO state FROM reports_report WHERE id=NEW.report_id;
  IF state<>'running' THEN RAISE EXCEPTION 'Only running reports may add source evidence'; END IF;
 ELSIF TG_TABLE_NAME IN ('reports_aibrief','reports_exportjob') THEN
  IF NEW.report_id IS NOT NULL THEN
   SELECT owner_id,status INTO owner_key,state FROM reports_report WHERE id=NEW.report_id;
   IF NEW.owner_id<>owner_key OR state<>'succeeded' THEN RAISE EXCEPTION 'Report must be completed and owned by requester'; END IF;
  END IF;
 ELSIF TG_TABLE_NAME='reports_briefrevision' THEN
  SELECT owner_id,status INTO owner_key,state FROM reports_aibrief WHERE id=NEW.brief_id;
  IF NEW.actor_id<>owner_key OR state<>'succeeded' THEN RAISE EXCEPTION 'Only owner may edit a completed brief'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION reports_analysis_source() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE state text;
BEGIN
 IF NEW.risk_run_id IS NOT NULL THEN
  SELECT status INTO state FROM analytics_riskrun WHERE id=NEW.risk_run_id;
  IF state<>'succeeded' THEN RAISE EXCEPTION 'Risk source is not completed'; END IF;
 END IF;
 IF NEW.forecast_run_id IS NOT NULL THEN
  SELECT status INTO state FROM forecasting_forecastrun WHERE id=NEW.forecast_run_id;
  IF state<>'succeeded' THEN RAISE EXCEPTION 'Forecast source is not completed'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER report_analysis_source BEFORE INSERT OR UPDATE ON reports_report
FOR EACH ROW EXECUTE FUNCTION reports_analysis_source();
"""

class Migration(migrations.Migration):
    dependencies=[('reports','0002_evidence_guards')]
    operations=[migrations.RunSQL(SQL,"DROP TRIGGER report_analysis_source ON reports_report; DROP FUNCTION reports_analysis_source();")]
