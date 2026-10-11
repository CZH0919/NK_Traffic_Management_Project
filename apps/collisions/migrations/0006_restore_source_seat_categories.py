from django.db import migrations

def restore_categories(apps,schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""UPDATE collisions_person p SET position_in_vehicle=btrim(r.payload->>'position_in_vehicle'),
            revision=revision+1,updated_at=CURRENT_TIMESTAMP FROM ingestion_stagedrecord r
            JOIN ingestion_snapshot s ON s.id=r.snapshot_id WHERE p.raw_record_id=r.id AND p.origin='official'
            AND p.revision=1 AND p.position_in_vehicle='' AND s.status<>'published'
            AND length(btrim(r.payload->>'position_in_vehicle'))>80""")
        affected=cursor.rowcount
        if affected:
            cursor.execute("""DELETE FROM ingestion_qualityissue q USING collisions_person p
                WHERE q.record_id=p.raw_record_id AND length(p.position_in_vehicle)>80
                AND q.code='invalid_text' AND q.details='{"fields":["position_in_vehicle"]}'::jsonb""")
            apps.get_model('core','AuditEvent').objects.using(schema_editor.connection.alias).create(
                actor_label='',action='data_changed',object_type='import',object_id='seat_category_schema',outcome='success',
                details={'count':affected,'fields':['position_in_vehicle'],'reason_code':'restore_full_official_category'})

class Migration(migrations.Migration):
    dependencies=[('collisions','0005_alter_person_position_in_vehicle'),('core','0002_reference_and_guards')]
    operations=[migrations.RunPython(restore_categories,migrations.RunPython.noop)]
