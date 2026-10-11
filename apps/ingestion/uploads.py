"""Bounded local-file validation. Originals and normalized pages are separate evidence."""
import csv
import gzip
import hashlib
import io
import json
import uuid
from datetime import date
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.core.audit import record_event
from apps.core.services import require_actor
from .models import ImportJob

from apps.core.storage import storage_root
ROOT=storage_root()
MAX_UPLOAD=512*1024*1024
MAX_EXPANDED=2*1024*1024*1024
MAX_RECORD=2*1024*1024
MAX_ROWS=5_000_000
DATASETS={'crashes':'h9gi-nx95','vehicles':'bm4k-52h4','persons':'f55k-p6yu','traffic':'7ym2-wayt'}
REQUIRED={'crashes':{'collision_id','crash_date','crash_time'},
    'vehicles':{'unique_id','collision_id','vehicle_id','crash_date','crash_time'},
    'persons':{'unique_id','collision_id','vehicle_id','crash_date','crash_time'},
    'traffic':{'source_row_id','requestid','segmentid','direction','yr','m','d','hh','mm','vol','wktgeom'}}
ERRORS={'unsupported_file':'文件类型不支持，请使用 UTF-8 CSV、JSON 数组或 JSONL，可使用 gzip 压缩。',
    'size_limit':'文件或解压后的内容超过允许范围。','invalid_encoding':'文件不是有效的 UTF-8 编码。',
    'invalid_format':'文件结构不合法；应为字段名表头的 CSV、对象数组或逐行 JSON 对象。',
    'schema_mismatch':'缺少必要字段，请按来源字段规范重新准备文件。','empty_file':'文件没有数据行。',
    'permission_revoked':'提交者的维护权限已撤销，任务未继续执行。',
    'source_disabled':'此来源已停用，任务未继续执行。','integrity_failed':'文件完整性校验失败，请重新上传原始文件。',
    'import_failed':'导入未完成，已提交的分批记录被保留；检查来源与关联后可重试。',
    'validation_failed':'文件校验未完成，请检查文件内容或重新上传。',
    'worker_interrupted':'处理进程中断，可从已提交的进度重试。','notice_missing':'流量来源的位置公告证据不可用，导入未执行。'}

class UploadError(ValueError):
    def __init__(self,code): self.code=code; super().__init__(code)

def safe_path(relative,base='data/raw/uploads'):
    path=(ROOT/relative).resolve()
    if not path.is_relative_to((ROOT/base).resolve()): raise UploadError('integrity_failed')
    return path

def extension(name):
    name=name.lower(); name=name[:-3] if name.endswith('.gz') else name
    suffix=Path(name).suffix
    if suffix not in ('.csv','.json','.jsonl','.ndjson'): raise UploadError('unsupported_file')
    return suffix

def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()

def submit_upload(actor,source,uploaded):
    require_actor(actor,'maintain_data')
    if not source.is_active: raise UploadError('source_disabled')
    if DATASETS.get(source.kind)!=source.dataset_id: raise UploadError('schema_mismatch')
    extension(uploaded.name)
    if uploaded.size>MAX_UPLOAD: raise UploadError('size_limit')
    identity=uuid.uuid4(); suffix=Path(uploaded.name).suffix.lower()
    if suffix=='.gz': suffix=extension(uploaded.name)+'.gz'
    relative=f'data/raw/uploads/{identity}/original{suffix}'; path=safe_path(relative)
    path.parent.mkdir(parents=True,exist_ok=False)
    digest=hashlib.sha256(); size=0
    with path.open('xb') as out:
        for chunk in uploaded.chunks():
            size+=len(chunk)
            if size>MAX_UPLOAD: raise UploadError('size_limit')
            digest.update(chunk); out.write(chunk)
    with transaction.atomic():
        job=ImportJob.objects.create(id=identity,source=source,created_by=actor,requested_by=actor,
            original_name=Path(uploaded.name).name[:200],relative_path=relative,file_size=size,checksum=digest.hexdigest())
        record_event(actor=actor,action='import_submitted',object_type='ImportJob',object_id=job.pk,details={'reason_code':'file_validation'})
    return job

class BoundedText:
    def __init__(self,stream): self.stream=stream; self.size=0
    def read(self,size=65536):
        value=self.stream.read(size); self.size+=len(value.encode('utf-8'))
        if self.size>MAX_EXPANDED: raise UploadError('size_limit')
        return value
    def __iter__(self): return self
    def __next__(self):
        value=self.stream.readline(MAX_RECORD+1)
        if not value: raise StopIteration
        self.size+=len(value.encode('utf-8'))
        if len(value)>MAX_RECORD or self.size>MAX_EXPANDED: raise UploadError('size_limit')
        return value

def array_rows(stream):
    decoder=json.JSONDecoder(); buffer=''; ended=False
    def take_nonspace():
        nonlocal buffer,ended
        while not buffer.strip() and not ended:
            more=stream.read(65536); ended=not more; buffer+=more
        buffer=buffer.lstrip()
    take_nonspace()
    if not buffer.startswith('['): raise UploadError('invalid_format')
    buffer=buffer[1:]; first=True
    while True:
        take_nonspace()
        if buffer.startswith(']'):
            buffer=buffer[1:]; break
        if not first:
            if not buffer.startswith(','): raise UploadError('invalid_format')
            buffer=buffer[1:]; take_nonspace()
            if buffer.startswith(']'): raise UploadError('invalid_format')
        while True:
            try: value,pos=decoder.raw_decode(buffer); break
            except json.JSONDecodeError:
                if ended or len(buffer)>MAX_RECORD: raise UploadError('invalid_format')
                more=stream.read(65536); ended=not more; buffer+=more
        if pos>MAX_RECORD: raise UploadError('size_limit')
        buffer=buffer[pos:]; first=False; yield value
    if buffer.strip() or any(chunk.strip() for chunk in iter(lambda:stream.read(65536),'')):
        raise UploadError('invalid_format')

def input_rows(path):
    suffix=extension(path.name)
    opener=gzip.open if path.name.lower().endswith('.gz') else open
    try:
        with opener(path,'rt',encoding='utf-8-sig',newline='') as file:
            stream=BoundedText(file)
            if suffix=='.csv':
                csv.field_size_limit(MAX_RECORD); reader=csv.DictReader(stream)
                fields=reader.fieldnames or []
                if not fields or len(set(fields))!=len(fields) or any(not f for f in fields): raise UploadError('invalid_format')
                rows=reader
            elif suffix=='.json': rows=array_rows(stream)
            else: rows=(json.loads(line) for line in stream if line.strip())
            for row in rows:
                if not isinstance(row,dict) or not row or None in row or any(not isinstance(k,str) for k in row): raise UploadError('invalid_format')
                if any(v is None for v in row.values()) and suffix=='.csv': raise UploadError('invalid_format')
                if '\\u0000' in json.dumps(row,ensure_ascii=False): raise UploadError('invalid_format')
                yield row
    except UnicodeError: raise UploadError('invalid_encoding') from None
    except (json.JSONDecodeError,csv.Error,EOFError,gzip.BadGzipFile): raise UploadError('invalid_format') from None

def validate_file(job,progress):
    path=safe_path(job.relative_path)
    if file_hash(path)!=job.checksum: raise UploadError('integrity_failed')
    run=uuid.uuid4().hex; outdir=path.parent/run; outdir.mkdir()
    files=[]; fields=set(); group=[]; group_bytes=0; count=0; first=None; last=None
    def flush():
        raw=json.dumps(group,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        page=outdir/f'page_{len(files)+1:05d}.json.gz'
        with gzip.open(page,'xb') as out: out.write(raw)
        files.append({'path':page.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(group)})
        group.clear(); progress(count)
    for row in input_rows(path):
        count+=1
        if count>MAX_ROWS: raise UploadError('size_limit')
        try: row_bytes=len(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))
        except (ValueError,TypeError): raise UploadError('invalid_format') from None
        if row_bytes>MAX_RECORD: raise UploadError('size_limit')
        if group and group_bytes+row_bytes>16*1024*1024: flush(); group_bytes=0
        fields.update(row)
        if len(fields)>200: raise UploadError('schema_mismatch')
        try:
            day=date(int(row['yr']),int(row['m']),int(row['d'])) if job.source.kind=='traffic' else date.fromisoformat(str(row['crash_date'])[:10])
            first=min(first,day) if first else day; last=max(last,day) if last else day
        except (ValueError,TypeError,KeyError): pass
        group.append(row); group_bytes+=row_bytes
        if len(group)>=10000: flush(); group_bytes=0
    if group: flush()
    if not count: raise UploadError('empty_file')
    if not REQUIRED[job.source.kind]<=fields: raise UploadError('schema_mismatch')
    manifest={'status':'complete','kind':job.source.kind,'dataset':job.source.dataset_id,'label':'uploaded_file',
        'captured_at':timezone.now().isoformat(),'source_url':job.source.homepage,'fields':sorted(fields),
        'period_start':first.isoformat() if first else None,'period_end':last.isoformat() if last else None,
        'version':'上传文件','row_count':count,'expected_rows':count,'files':files,
        'checksum':hashlib.sha256(''.join(f['sha256'] for f in files).encode()).hexdigest(),
        'original_file':job.relative_path,'original_checksum':job.checksum,'uploader_id':job.created_by_id,
        'scope':'user_supplied_file_only'}
    manifest_path=ROOT/f'data/manifests/uploads/{job.pk}/{run}.json'; manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open('x',encoding='utf-8') as out: json.dump(manifest,out,ensure_ascii=False,indent=2)
    return manifest_path.relative_to(ROOT).as_posix(),{'rows':count,'fields':sorted(fields),
        'period_start':manifest['period_start'],'period_end':manifest['period_end'],'scope':'仅此次上传文件；不声明来源的全量覆盖'}
