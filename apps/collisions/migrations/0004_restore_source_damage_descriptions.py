"""Repair only unmodified, unpublished imports affected by the original 200-char limit."""
from django.db import migrations

def restore_descriptions(apps,schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""UPDATE collisions_vehicle v SET public_property_damage_type=btrim(r.payload->>'public_property_damage_type'),
            revision=revision+1,updated_at=CURRENT_TIMESTAMP FROM ingestion_stagedrecord r
            JOIN ingestion_snapshot s ON s.id=r.snapshot_id WHERE v.raw_record_id=r.id AND v.origin='official'
            AND v.revision=1 AND v.public_property_damage_type='' AND s.status<>'published'
            AND length(btrim(r.payload->>'public_property_damage_type'))>200 RETURNING r.id""")
        raw_ids=[row[0] for row in cursor.fetchall()]
        if raw_ids:
            # This was an application schema limit, not an invalid official source value.
            cursor.execute("""DELETE FROM ingestion_qualityissue WHERE record_id=ANY(%s)
                AND code='invalid_text' AND details='{"fields":["public_property_damage_type"]}'::jsonb""",[raw_ids])
            apps.get_model('core','AuditEvent').objects.using(schema_editor.connection.alias).create(
                actor_label='',action='data_changed',object_type='import',object_id='damage_description_schema',outcome='success',
                details={'count':len(raw_ids),'fields':['public_property_damage_type'],'reason_code':'restore_full_official_text'})

class Migration(migrations.Migration):
    dependencies=[('collisions','0003_alter_vehicle_public_property_damage_type'),('core','0002_reference_and_guards')]
    operations=[migrations.RunPython(restore_descriptions,migrations.RunPython.noop)]
