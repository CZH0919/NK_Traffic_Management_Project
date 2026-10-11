from django.db import migrations

SQL="""
CREATE FUNCTION traffic_maintenance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status IN ('succeeded','failed') THEN
  RAISE EXCEPTION 'Finished maintenance evidence is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER maintenance_evidence BEFORE UPDATE OR DELETE ON core_maintenancerun
FOR EACH ROW EXECUTE FUNCTION traffic_maintenance_guard();
"""

class Migration(migrations.Migration):
    dependencies=[('core','0008_maintenancerun')]
    operations=[migrations.RunSQL(SQL,'DROP TRIGGER maintenance_evidence ON core_maintenancerun; DROP FUNCTION traffic_maintenance_guard();')]
