from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('network','0001_initial')]
    operations = [migrations.RunSQL("""
ALTER TABLE network_junction ADD CONSTRAINT junction_id_network_pair UNIQUE(id,network_id);
ALTER TABLE network_roadsegment ADD CONSTRAINT road_from_same_network FOREIGN KEY(from_node_id,network_id) REFERENCES network_junction(id,network_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE network_roadsegment ADD CONSTRAINT road_to_same_network FOREIGN KEY(to_node_id,network_id) REFERENCES network_junction(id,network_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE network_junction ADD CONSTRAINT junction_point_range CHECK(NOT ST_IsEmpty(geom) AND ST_X(geom) BETWEEN -180 AND 180 AND ST_Y(geom) BETWEEN -90 AND 90);
ALTER TABLE network_roadsegment ADD CONSTRAINT road_geometry_valid CHECK(NOT ST_IsEmpty(geom) AND ST_IsValid(geom));
""", """
ALTER TABLE network_roadsegment DROP CONSTRAINT road_geometry_valid;
ALTER TABLE network_junction DROP CONSTRAINT junction_point_range;
ALTER TABLE network_roadsegment DROP CONSTRAINT road_from_same_network;
ALTER TABLE network_roadsegment DROP CONSTRAINT road_to_same_network;
ALTER TABLE network_junction DROP CONSTRAINT junction_id_network_pair;
""")]
