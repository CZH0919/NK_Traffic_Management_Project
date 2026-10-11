"""Local, audited backup bundles and new-database-only restore verification."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path,PurePosixPath
import psycopg
import psutil
from psycopg import sql
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from .audit import record_event
from .models import MaintenanceRun
from .services import require_actor
from .storage import storage_root

FILE_TREES=('data/raw','data/manifests','var/exports')
ERRORS={'busy':'已有数据处理或维护正在执行，请先结束任务并停止本机应用。',
 'unsafe_target':'恢复目标必须是新的隔离验证库，不能使用业务库、测试库或已存在数据库。',
 'invalid_backup':'备份来源、文件范围或完整性检查未通过。',
 'tools':'PostgreSQL 维护工具不可用，请核对已有安装目录。',
 'dump_failed':'数据库备份未完成；已保留本次失败记录。',
 'restore_failed':'隔离恢复未完成，原业务库未被覆盖；失败的隔离库保留供核查。',
 'verification':'恢复核对未通过，不能将该副本用于验收。',
 'files':'文件备份或恢复未完成，请核对可用空间与本地文件状态。',
 'database':'数据库连接或查询未完成，请核对本机服务与连接配置。',
 'permission':'执行账户没有运行维护权限。','interrupted':'维护被中断；原业务库未被覆盖。',
 'internal':'维护未完成，请查看运行记录并核对本机配置。'}


class MaintenanceError(Exception):
    def __init__(self,code):self.code=code;super().__init__(ERRORS.get(code,ERRORS['internal']))


def dump_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def params(database=None,admin_password=None):
    db=settings.DATABASES['default']
    if db['HOST'] not in ('127.0.0.1','localhost','::1'):raise MaintenanceError('unsafe_target')
    return dict(host=db['HOST'],port=db['PORT'],dbname=database or db['NAME'],
        user='postgres' if admin_password is not None else db['USER'],
        password=admin_password if admin_password is not None else db['PASSWORD'],connect_timeout=5)


def target_name(value):
    db=settings.DATABASES['default']
    if not re.fullmatch(r'restore_nk_traffic_[a-z0-9_]{1,40}',value or '') or value in (db['NAME'],db['TEST']['NAME']):raise MaintenanceError('unsafe_target')
    return value


def tools_at(folder):
    folder=Path(folder).resolve()
    values={name:folder/(name+('.exe' if os.name=='nt' else '')) for name in ('pg_dump','pg_restore')}
    if not all(p.is_file() for p in values.values()):raise MaintenanceError('tools')
    return values


def run_tool(executable,arguments,db=None,timeout=7200,code='tools'):
    env=os.environ.copy();args=[str(executable)]
    if db:
        env['PGPASSWORD']=db['password']
        args+=['--host',str(db['host']),'--port',str(db['port']),'--username',db['user'],'--no-password']
    try:
        result=subprocess.run(args+list(arguments),env=env,capture_output=True,timeout=timeout)
        if result.returncode:raise MaintenanceError(code)
        return result.stdout
    except (OSError,subprocess.TimeoutExpired) as e:raise MaintenanceError(code) from e


@contextmanager
def data_lock(conn):
    locked=conn.execute('SELECT pg_try_advisory_lock(%s,%s)',(20261004,2)).fetchone()[0]
    if not locked:raise MaintenanceError('busy')
    try:yield
    finally:conn.execute('SELECT pg_advisory_unlock(%s,%s)',(20261004,2))


def assert_quiet(conn):
    # Import validation and import queues have distinct persisted state names.
    if conn.execute("SELECT EXISTS(SELECT 1 FROM ingestion_importjob WHERE status IN ('queued_validation','validating','queued_import','running'))").fetchone()[0]:raise MaintenanceError('busy')
    for table in ('analytics_riskrun','forecasting_forecastrun','reports_report','reports_exportjob','reports_aibrief'):
        if conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE status IN ('queued','running'))").format(sql.Identifier(table))).fetchone()[0]:raise MaintenanceError('busy')


def assert_managed_offline():
    for path in (settings.BASE_DIR/'var/runtime').glob('local_*.json'):
        try:
            state=json.loads(path.read_text(encoding='utf-8'))
            process=psutil.Process(state['pid'])
            if process.create_time()==state['created_at']:raise MaintenanceError('busy')
        except psutil.NoSuchProcess:pass
        except (ValueError,KeyError,OSError) as error:raise MaintenanceError('busy') from error


def inventory(conn):
    """Counts plus relational, calendar, geometry, index and PostGIS checks."""
    names=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'spatial_ref_sys' ORDER BY tablename")]
    counts={name:conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0] for name in names}
    months=conn.execute("""SELECT to_char(local_date,'YYYY-MM'),count(*),sum(persons_injured),sum(persons_killed)
     FROM collisions_collision WHERE origin='official' AND is_active GROUP BY 1 ORDER BY 1""").fetchall()
    checks={
      'person_vehicle_mismatch':conn.execute('SELECT count(*) FROM collisions_person p JOIN collisions_vehicle v ON v.id=p.vehicle_id WHERE p.collision_id IS DISTINCT FROM v.collision_id').fetchone()[0],
      'invalid_road_geometry':conn.execute('SELECT count(*) FROM network_roadsegment WHERE NOT ST_IsValid(geom) OR ST_SRID(geom)<>4326').fetchone()[0],
      'unvalidated_constraints':conn.execute("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated").fetchone()[0],
      'invalid_indexes':conn.execute("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT i.indisvalid").fetchone()[0],
    }
    distance=conn.execute("SELECT ST_Distance(ST_SetSRID(ST_Point(-73.98,40.70),4326)::geography,ST_SetSRID(ST_Point(-73.98,40.71),4326)::geography)").fetchone()[0]
    if any(checks.values()) or not 1100<distance<1120:raise MaintenanceError('verification')
    revisions=conn.execute('SELECT coalesce(max(id),0) FROM core_datarevision').fetchone()[0]
    return {'table_counts':counts,'monthly_crashes':months,'checks':checks,'revision':revisions,
            'postgis':conn.execute('SELECT PostGIS_Lib_Version()').fetchone()[0], 'distance_m':round(distance,4)}


def file_inventory(root):
    result=[]
    for folder in FILE_TREES:
        base=(root/folder).resolve()
        for path in sorted(base.rglob('*')) if base.exists() else []:
            if path.is_symlink() or not path.resolve().is_relative_to(base):raise MaintenanceError('files')
            if path.is_file():result.append({'path':path.relative_to(root).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)})
    return result


def write_files(root,path,entries):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for entry in entries:
            source=root/entry['path'];archive.write(source,entry['path'])
            if source.stat().st_size!=entry['bytes'] or digest(source)!=entry['sha256']:raise MaintenanceError('files')


def safe_member(name):
    path=PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or any(p in ('..','.') for p in name.split('/')):raise MaintenanceError('invalid_backup')
    if not any(name.startswith(prefix+'/') for prefix in FILE_TREES):raise MaintenanceError('invalid_backup')
    return path


def extract_files(archive_path,destination,entries):
    destination.mkdir(parents=True,exist_ok=False)
    expected={row['path']:row for row in entries}
    if len(expected)!=len(entries):raise MaintenanceError('invalid_backup')
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.namelist())!=len(expected) or set(archive.namelist())!=set(expected):raise MaintenanceError('invalid_backup')
        for member in archive.infolist():
            relative=safe_member(member.filename);entry=expected[member.filename]
            if member.file_size!=entry['bytes']:raise MaintenanceError('invalid_backup')
            target=(destination/Path(relative)).resolve()
            if not target.is_relative_to(destination.resolve()):raise MaintenanceError('invalid_backup')
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(member) as src,target.open('xb') as dest:shutil.copyfileobj(src,dest,1024*1024)
            if digest(target)!=entry['sha256']:raise MaintenanceError('invalid_backup')


@transaction.atomic
def begin(actor,operation,target,source=None):
    require_actor(actor,'manage_system')
    run=MaintenanceRun.objects.create(actor=actor,operation=operation,target_label=target,source_run=source)
    record_event(actor=actor,action='maintenance_started',object_type='MaintenanceRun',object_id=run.pk)
    return run


@transaction.atomic
def finish(run,summary,code=''):
    run.status='failed' if code else 'succeeded';run.error_code=code;run.summary=summary;run.finished_at=timezone.now()
    run.save()
    record_event(actor=run.actor,action='maintenance_failed' if code else 'maintenance_finished',object_type='MaintenanceRun',object_id=run.pk,
        outcome='failure' if code else 'success',details={'reason_code':code or 'verified'})


def fail(run,summary,error):
    code=error.code if isinstance(error,MaintenanceError) else 'permission' if isinstance(error,PermissionDenied) else 'database' if isinstance(error,psycopg.Error) else 'interrupted' if isinstance(error,KeyboardInterrupt) else 'internal'
    finish(run,summary,code)
    raise MaintenanceError(code) from error


def backup(actor,tool_dir):
    require_actor(actor,'manage_system');programs=tools_at(tool_dir);assert_managed_offline()
    run=begin(actor,'backup',settings.DATABASES['default']['NAME']);summary={};start=time.monotonic()
    folder=(settings.BASE_DIR/'var/backups'/str(run.pk)).resolve()
    try:
        folder.mkdir(parents=True,exist_ok=False)
        run.artifact_relative_path=folder.relative_to(settings.BASE_DIR).as_posix();run.save(update_fields=['artifact_relative_path'])
        with psycopg.connect(**params()) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with data_lock(conn):
                assert_quiet(conn)
                snapshot=conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
                baseline=inventory(conn)
                print('Database snapshot verified; writing backup.',flush=True)
                run_tool(programs['pg_dump'],['--format=custom','--dbname',params()['dbname'],'--snapshot',snapshot,'--lock-wait-timeout=10s','--file',str(folder/'database.dump')],params(),code='dump_failed')
                listing=run_tool(programs['pg_restore'],['--list',str(folder/'database.dump')])
                if b'TABLE DATA' not in listing:raise MaintenanceError('invalid_backup')
                entries=file_inventory(storage_root());write_files(storage_root(),folder/'files.zip',entries)
                if file_inventory(storage_root())!=entries:raise MaintenanceError('files')
                manifest={'schema':1,'backup_id':str(run.pk),'source_database':run.target_label,'captured_at':run.started_at.isoformat(),
                    'database':{'sha256':digest(folder/'database.dump'),'bytes':(folder/'database.dump').stat().st_size},
                    'files_archive':{'sha256':digest(folder/'files.zip'),'bytes':(folder/'files.zip').stat().st_size},
                    'files':entries,'database_inventory':baseline,'excludes':['private credential files','local environment configuration','source code','runtime locks','other backups','development evidence']}
                dump_json(folder/'manifest.json',manifest)
        summary={'manifest_sha256':digest(folder/'manifest.json'),'file_count':len(entries),'file_bytes':sum(r['bytes'] for r in entries),
                 'database_bytes':manifest['database']['bytes'],'table_count':len(baseline['table_counts']),'archive_verified':True,
                 'elapsed_seconds':round(time.monotonic()-start,2),'scope':'数据库、原始快照、上传文件、来源清单和完成附件；配置／凭据另行保管。'}
        finish(run,summary);dump_json(folder/'receipt.json',{'id':str(run.pk),'status':run.status,'summary':summary})
        return run
    except (Exception,KeyboardInterrupt) as error:fail(run,summary,error)


def checked_bundle(source):
    expected=(settings.BASE_DIR/'var/backups'/str(source.pk)).resolve()
    if source.operation!='backup' or source.status!='succeeded' or source.artifact_relative_path!=expected.relative_to(settings.BASE_DIR).as_posix():raise MaintenanceError('invalid_backup')
    if digest(expected/'manifest.json')!=source.summary.get('manifest_sha256'):raise MaintenanceError('invalid_backup')
    manifest=json.loads((expected/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=1 or manifest.get('backup_id')!=str(source.pk):raise MaintenanceError('invalid_backup')
    for filename,key in [('database.dump','database'),('files.zip','files_archive')]:
        file=expected/filename
        if file.stat().st_size!=manifest[key]['bytes'] or digest(file)!=manifest[key]['sha256']:raise MaintenanceError('invalid_backup')
    for entry in manifest['files']:safe_member(entry['path'])
    return expected,manifest


def restore(actor,source,target,tool_dir,admin_password_file):
    require_actor(actor,'manage_system');target=target_name(target);programs=tools_at(tool_dir)
    folder,manifest=checked_bundle(source)
    password=Path(admin_password_file).read_text(encoding='utf-8-sig').strip()
    admin=params('postgres',password)
    with psycopg.connect(**admin,autocommit=True) as conn:
        if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(target,)).fetchone():raise MaintenanceError('unsafe_target')
    run=begin(actor,'restore',target,source);summary={};start=time.monotonic()
    destination=(settings.BASE_DIR/'var/restores'/str(run.pk)).resolve()
    try:
        destination.mkdir(parents=True,exist_ok=False)
        run.artifact_relative_path=destination.relative_to(settings.BASE_DIR).as_posix();run.save(update_fields=['artifact_relative_path'])
        with psycopg.connect(**admin,autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(target),sql.Identifier(settings.DATABASES['default']['USER'])))
            conn.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(target),sql.Literal('traffic-restored-copy:'+str(run.pk))))
        summary['database_created']=True
        print('New isolated database created; restoring archive.',flush=True)
        # Trusted locally generated archive only. Restoring as administrator keeps
        # original object ownership, including the administrator-owned extension.
        run_tool(programs['pg_restore'],['--dbname',target,'--exit-on-error','--no-privileges','--jobs=2',str(folder/'database.dump')],params(target,password),code='restore_failed')
        with psycopg.connect(**params(target)) as conn:
            conn.execute('ANALYZE')
            actual=inventory(conn)
            if json.loads(json.dumps(actual,default=str))!=manifest['database_inventory']:raise MaintenanceError('verification')
            # The source dump necessarily captured its own backup before finish.
            # Reconcile that one operational record from its verified receipt.
            conn.execute("UPDATE core_maintenancerun SET status='succeeded',summary=%s,finished_at=%s WHERE id=%s AND status='running'",(json.dumps(source.summary,ensure_ascii=False),source.finished_at,source.pk))
        extract_files(folder/'files.zip',destination/'files',manifest['files'])
        summary.update(file_count=len(manifest['files']),table_count=len(actual['table_counts']),inventory_matched=True,
            relationships_valid=True,spatial_functions_valid=True,files_verified=True,
            elapsed_seconds=round(time.monotonic()-start,2),scope='新的隔离验证库及独立文件副本；主业务库与原文件未覆盖。')
        dump_json(destination/'verification.json',{'source_backup':str(source.pk),'restore_id':str(run.pk),'inventory':actual,'summary':summary})
        finish(run,summary);return run
    except (Exception,KeyboardInterrupt) as error:fail(run,summary,error)


def restored_environment(identity):
    run=MaintenanceRun.objects.get(pk=identity,operation='restore',status='succeeded')
    target=target_name(run.target_label)
    root=(settings.BASE_DIR/'var/restores'/str(run.pk)/'files').resolve()
    if not root.is_dir() or not run.summary.get('files_verified'):raise MaintenanceError('verification')
    with psycopg.connect(**params(target)) as conn:
        marker=conn.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
    if marker!='traffic-restored-copy:'+str(run.pk):raise MaintenanceError('unsafe_target')
    return {'DB_NAME':target,'TRAFFIC_STORAGE_ROOT':str(root),'TRAFFIC_ISOLATED_COPY':'1'}
