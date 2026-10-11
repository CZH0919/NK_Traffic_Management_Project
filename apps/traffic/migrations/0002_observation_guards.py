from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('traffic','0001_initial')]
    operations = [migrations.RunSQL("""
ALTER TABLE traffic_flowconflict ADD CONSTRAINT conflict_identity_pair UNIQUE(id,site_id,local_date,local_time);
ALTER TABLE traffic_trafficobservation ADD CONSTRAINT observation_conflict_same_slot FOREIGN KEY(conflict_id,site_id,local_date,local_time) REFERENCES traffic_flowconflict(id,site_id,local_date,local_time) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE traffic_trafficobservation ADD CONSTRAINT observation_quarter_hour CHECK(EXTRACT(MINUTE FROM local_time)::int % 15 = 0 AND EXTRACT(SECOND FROM local_time) = 0);
ALTER TABLE traffic_samplingsite ADD CONSTRAINT site_point_range CHECK(geom IS NULL OR (NOT ST_IsEmpty(geom) AND ST_X(geom) BETWEEN -180 AND 180 AND ST_Y(geom) BETWEEN -90 AND 90));
""", """
ALTER TABLE traffic_samplingsite DROP CONSTRAINT site_point_range;
ALTER TABLE traffic_trafficobservation DROP CONSTRAINT observation_quarter_hour;
ALTER TABLE traffic_trafficobservation DROP CONSTRAINT observation_conflict_same_slot;
ALTER TABLE traffic_flowconflict DROP CONSTRAINT conflict_identity_pair;
""")]
