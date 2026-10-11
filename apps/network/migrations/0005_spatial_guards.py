from django.db import migrations

SQL="""
CREATE OR REPLACE FUNCTION guard_network_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME='network_roadsegment' THEN
    IF TG_OP='UPDATE' AND ROW(NEW.network_id,NEW.source_feature_id,NEW.source_segment_id,ST_AsEWKB(NEW.geom))
       IS DISTINCT FROM ROW(OLD.network_id,OLD.source_feature_id,OLD.source_segment_id,ST_AsEWKB(OLD.geom)) THEN
      RAISE EXCEPTION 'Road source identity and geometry are immutable';
    END IF;
    IF EXISTS(SELECT 1 FROM network_junction n WHERE n.id IN (NEW.from_node_id,NEW.to_node_id) AND n.network_id<>NEW.network_id)
       OR EXISTS(SELECT 1 FROM network_roadsegment r WHERE r.id=NEW.canonical_id AND r.network_id<>NEW.network_id) THEN
      RAISE EXCEPTION 'Road references must belong to the same network';
    END IF;
  ELSIF TG_TABLE_NAME='network_junction' THEN
    IF TG_OP='UPDATE' AND ROW(NEW.network_id,NEW.source_node_id,ST_AsEWKB(NEW.geom))
      IS DISTINCT FROM ROW(OLD.network_id,OLD.source_node_id,ST_AsEWKB(OLD.geom)) THEN
      RAISE EXCEPTION 'Node source identity and geometry are immutable';
    END IF;
  ELSE
    IF EXISTS(SELECT 1 FROM network_roadsegment r WHERE r.id=NEW.road_id AND r.network_id<>NEW.network_id) THEN
      RAISE EXCEPTION 'Spatial target must belong to the same network';
    END IF;
    IF TG_TABLE_NAME='network_spatialmatch' THEN
      IF EXISTS(SELECT 1 FROM network_junction n WHERE n.id=NEW.junction_id AND n.network_id<>NEW.network_id) THEN
        RAISE EXCEPTION 'Spatial junction must belong to the same network';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guard_road_links BEFORE INSERT OR UPDATE ON network_roadsegment FOR EACH ROW EXECUTE FUNCTION guard_network_links();
CREATE TRIGGER guard_node_source BEFORE UPDATE ON network_junction FOR EACH ROW EXECUTE FUNCTION guard_network_links();
CREATE TRIGGER guard_collision_match BEFORE INSERT OR UPDATE ON network_spatialmatch FOR EACH ROW EXECUTE FUNCTION guard_network_links();
CREATE TRIGGER guard_site_match BEFORE INSERT OR UPDATE ON network_samplingroadmatch FOR EACH ROW EXECUTE FUNCTION guard_network_links();
"""
REVERSE="""
DROP TRIGGER guard_road_links ON network_roadsegment;
DROP TRIGGER guard_node_source ON network_junction;
DROP TRIGGER guard_collision_match ON network_spatialmatch;
DROP TRIGGER guard_site_match ON network_samplingroadmatch;
DROP FUNCTION guard_network_links();
"""
class Migration(migrations.Migration):
    dependencies=[('network','0004_roadsegment_bearing_deg')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
