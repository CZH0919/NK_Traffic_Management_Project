from django.db import migrations

FORWARD="""
CREATE FUNCTION core_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Data revision evidence is immutable'; END $$;
CREATE TRIGGER core_revision_guard BEFORE UPDATE OR DELETE ON core_datarevision
FOR EACH ROW EXECUTE FUNCTION core_revision_immutable();
CREATE FUNCTION core_mapping_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Category versions are retained'; END IF;
 IF ROW(NEW.kind,NEW.raw_value,NEW.canonical_code,NEW.canonical_label,NEW.version,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.kind,OLD.raw_value,OLD.canonical_code,OLD.canonical_label,OLD.version,OLD.created_at)
 THEN RAISE EXCEPTION 'Create a new category version instead of overwriting it'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER core_mapping_guard BEFORE UPDATE OR DELETE ON core_categorymapping
FOR EACH ROW EXECUTE FUNCTION core_mapping_immutable();
"""
REVERSE="""
DROP TRIGGER core_revision_guard ON core_datarevision;
DROP FUNCTION core_revision_immutable();
DROP TRIGGER core_mapping_guard ON core_categorymapping;
DROP FUNCTION core_mapping_immutable();
"""
class Migration(migrations.Migration):
    dependencies=[('core','0003_categorymapping_datarevision_savedquery')]
    operations=[migrations.RunSQL(FORWARD,REVERSE)]
