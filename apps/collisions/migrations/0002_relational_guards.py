from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('collisions','0001_initial')]
    operations = [migrations.RunSQL("""
ALTER TABLE collisions_person ADD CONSTRAINT person_vehicle_same_collision
FOREIGN KEY (vehicle_id, collision_id) REFERENCES collisions_vehicle (id, collision_id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE collisions_collision ADD CONSTRAINT collision_point_range
CHECK (geom IS NULL OR (NOT ST_IsEmpty(geom) AND ST_X(geom) BETWEEN -180 AND 180 AND ST_Y(geom) BETWEEN -90 AND 90));
""", """
ALTER TABLE collisions_collision DROP CONSTRAINT collision_point_range;
ALTER TABLE collisions_person DROP CONSTRAINT person_vehicle_same_collision;
""")]
