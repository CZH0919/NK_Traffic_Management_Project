from django.db import migrations

SOURCES = [
    ('nyc-crashes','交通事故事件','NYPD','h9gi-nx95','crashes','https://data.cityofnewyork.us/d/h9gi-nx95','警方报告的事故事件、发生时间、位置及事故级伤亡统计。位置或行政区缺失不等于事故不存在。'),
    ('nyc-vehicles','涉事车辆','NYPD','bm4k-52h4','vehicles','https://data.cityofnewyork.us/d/bm4k-52h4','车辆属性及事故关联。车辆唯一记录编号与来源中的车辆 UUID 分别保留。'),
    ('nyc-persons','涉事人员','NYPD','f55k-p6yu','persons','https://data.cityofnewyork.us/d/f55k-p6yu','事故相关人员的类别与伤害统计属性，不包含姓名、证件或联系方式。'),
    ('nyc-traffic','采样交通流量','NYC DOT','7ym2-wayt','traffic','https://data.cityofnewyork.us/d/7ym2-wayt','15 分钟采样计数。观测地点和时段不连续，不能将缺测视为零流量，也不能直接外推全年交通量。'),
    ('nyc-lion','LION 道路网络','NYC DCP','2v4z-66xt','roads','https://www.nyc.gov/content/planning/pages/resources/datasets/lion','道路网络及来源路段编号。使用的发布版本、坐标系与匹配方法随空间快照登记。'),
    ('nyc-lion-nodes','LION 道路节点','NYC DCP','2v4z-66xt','nodes','https://www.nyc.gov/content/planning/pages/resources/datasets/lion','与道路网络配套的空间节点。节点不自动等同于实际交叉路口，需结合拓扑与分类核验。'),
]

def seed(apps, schema_editor):
    Source = apps.get_model('ingestion','DataSource')
    Borough = apps.get_model('core','Borough')
    alias = schema_editor.connection.alias
    for code,name,publisher,dataset,kind,url,description in SOURCES:
        Source.objects.using(alias).update_or_create(code=code,defaults=dict(name=name,publisher=publisher,dataset_id=dataset,kind=kind,homepage=url,description=description,license_note='使用时保留 NYC Open Data / 发布机构署名，具体条款与版本随来源快照记录。'))
    for code,name,chinese in [(1,'MANHATTAN','曼哈顿'),(2,'BRONX','布朗克斯'),(3,'BROOKLYN','布鲁克林'),(4,'QUEENS','皇后区'),(5,'STATEN ISLAND','斯塔滕岛')]:
        Borough.objects.using(alias).update_or_create(code=code,defaults={'name':name,'chinese_name':chinese})

def unseed(apps, schema_editor):
    apps.get_model('core','Borough').objects.using(schema_editor.connection.alias).all().delete()
    apps.get_model('ingestion','DataSource').objects.using(schema_editor.connection.alias).filter(code__in=[s[0] for s in SOURCES]).delete()

FORWARD = """
CREATE FUNCTION traffic_deny_evidence_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Immutable evidence cannot be updated or deleted';
END $$;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON core_auditevent
FOR EACH ROW EXECUTE FUNCTION traffic_deny_evidence_change();
CREATE TRIGGER field_change_immutable BEFORE UPDATE OR DELETE ON core_fieldchange
FOR EACH ROW EXECUTE FUNCTION traffic_deny_evidence_change();
CREATE FUNCTION traffic_protect_raw() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Raw evidence cannot be deleted'; END IF;
  IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.snapshot_id <> OLD.snapshot_id
     OR NEW.row_number <> OLD.row_number OR NEW.source_key IS DISTINCT FROM OLD.source_key THEN
    RAISE EXCEPTION 'Raw evidence payload and identity are immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER raw_immutable BEFORE UPDATE OR DELETE ON ingestion_stagedrecord
FOR EACH ROW EXECUTE FUNCTION traffic_protect_raw();
ALTER TABLE core_borough ADD CONSTRAINT borough_geom_valid CHECK (geom IS NULL OR (NOT ST_IsEmpty(geom) AND ST_IsValid(geom)));
"""
REVERSE = """
ALTER TABLE core_borough DROP CONSTRAINT borough_geom_valid;
DROP TRIGGER raw_immutable ON ingestion_stagedrecord;
DROP FUNCTION traffic_protect_raw();
DROP TRIGGER field_change_immutable ON core_fieldchange;
DROP TRIGGER audit_immutable ON core_auditevent;
DROP FUNCTION traffic_deny_evidence_change();
"""

class Migration(migrations.Migration):
    dependencies = [('core','0001_initial')]
    # Define constraints before inserting rows: deferred FK events prevent ALTER
    # TABLE on the same table after data writes inside an atomic migration.
    operations = [migrations.RunSQL(FORWARD,REVERSE), migrations.RunPython(seed,unseed)]
