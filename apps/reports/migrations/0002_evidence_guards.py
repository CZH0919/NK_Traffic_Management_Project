from django.db import migrations

SQL="""
CREATE TRIGGER report_immutable BEFORE UPDATE OR DELETE ON reports_report
FOR EACH ROW EXECUTE FUNCTION analytics_run_guard();
CREATE TRIGGER export_immutable BEFORE UPDATE OR DELETE ON reports_exportjob
FOR EACH ROW EXECUTE FUNCTION analytics_run_guard();
CREATE TRIGGER report_source_immutable BEFORE UPDATE OR DELETE ON reports_reportsnapshot
FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE TRIGGER brief_revision_immutable BEFORE UPDATE OR DELETE ON reports_briefrevision
FOR EACH ROW EXECUTE FUNCTION analytics_immutable();
CREATE FUNCTION reports_relations() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_key bigint; state text;
BEGIN
 IF TG_TABLE_NAME='reports_reportsnapshot' THEN
  SELECT status INTO state FROM reports_report WHERE id=NEW.report_id;
  IF state<>'running' THEN RAISE EXCEPTION 'Only running reports may add source evidence'; END IF;
 ELSIF TG_TABLE_NAME IN ('reports_aibrief','reports_exportjob') AND NEW.report_id IS NOT NULL THEN
  SELECT owner_id,status INTO owner_key,state FROM reports_report WHERE id=NEW.report_id;
  IF NEW.owner_id<>owner_key OR state<>'succeeded' THEN RAISE EXCEPTION 'Report must be completed and owned by requester'; END IF;
 ELSIF TG_TABLE_NAME='reports_briefrevision' THEN
  SELECT owner_id,status INTO owner_key,state FROM reports_aibrief WHERE id=NEW.brief_id;
  IF NEW.actor_id<>owner_key OR state<>'succeeded' THEN RAISE EXCEPTION 'Only owner may edit a completed brief'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER report_source_scope BEFORE INSERT ON reports_reportsnapshot FOR EACH ROW EXECUTE FUNCTION reports_relations();
CREATE TRIGGER brief_report_scope BEFORE INSERT OR UPDATE ON reports_aibrief FOR EACH ROW EXECUTE FUNCTION reports_relations();
CREATE TRIGGER export_report_scope BEFORE INSERT OR UPDATE ON reports_exportjob FOR EACH ROW EXECUTE FUNCTION reports_relations();
CREATE TRIGGER brief_revision_scope BEFORE INSERT ON reports_briefrevision FOR EACH ROW EXECUTE FUNCTION reports_relations();
CREATE FUNCTION reports_brief_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Brief history cannot be deleted'; END IF;
 IF OLD.status='succeeded' THEN
  IF (to_jsonb(NEW)-ARRAY['edited_text','revision','edited_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['edited_text','revision','edited_at'])
   OR NEW.revision<>OLD.revision+1 OR NEW.edited_at IS NULL
  THEN RAISE EXCEPTION 'Generated brief evidence is immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER brief_evidence_guard BEFORE UPDATE OR DELETE ON reports_aibrief FOR EACH ROW EXECUTE FUNCTION reports_brief_guard();
CREATE FUNCTION reports_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status IN ('succeeded','failed') THEN RAISE EXCEPTION 'Finished API attempts cannot be changed'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ai_request_guard BEFORE UPDATE OR DELETE ON reports_airequest FOR EACH ROW EXECUTE FUNCTION reports_request_guard();
"""

REVERSE="""
DROP TRIGGER ai_request_guard ON reports_airequest;
DROP FUNCTION reports_request_guard();
DROP TRIGGER brief_evidence_guard ON reports_aibrief;
DROP FUNCTION reports_brief_guard();
DROP TRIGGER brief_revision_scope ON reports_briefrevision;
DROP TRIGGER export_report_scope ON reports_exportjob;
DROP TRIGGER brief_report_scope ON reports_aibrief;
DROP TRIGGER report_source_scope ON reports_reportsnapshot;
DROP FUNCTION reports_relations();
DROP TRIGGER brief_revision_immutable ON reports_briefrevision;
DROP TRIGGER report_source_immutable ON reports_reportsnapshot;
DROP TRIGGER export_immutable ON reports_exportjob;
DROP TRIGGER report_immutable ON reports_report;
"""

class Migration(migrations.Migration):
    dependencies=[('reports','0001_initial'),('analytics','0002_rules_and_evidence')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
