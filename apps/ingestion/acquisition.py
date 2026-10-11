"""Bounded, restartable acquisition of immutable official source snapshots."""
from __future__ import annotations
import csv
import gzip
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[2]
CATALOG = {'crashes':('h9gi-nx95','collision_id'), 'vehicles':('bm4k-52h4','unique_id'),
           'persons':('f55k-p6yu','unique_id'), 'traffic':('7ym2-wayt',':id')}

def utcnow(): return datetime.now(timezone.utc).isoformat()

def save_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.part')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def get(client, url, params=None):
    last=None
    for attempt in range(4):
        try:
            response=client.get(url,params=params)
            response.raise_for_status()
            return response
        except (httpx.TransportError,httpx.HTTPStatusError) as exc:
            last=exc
            if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code not in (408,429,500,502,503,504): raise
            if attempt<3: time.sleep(2**(attempt+1))
    raise RuntimeError(f'Official endpoint unavailable after retries: {type(last).__name__}') from last

def acquire_socrata(kind, start, end, label, page_size=50000):
    if kind not in CATALOG or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',label): raise ValueError('Invalid dataset or snapshot label')
    if not 1<=page_size<=100000: raise ValueError('page size outside supported range')
    dataset,key=CATALOG[kind]
    folder=ROOT/'data/raw'/label/kind; folder.mkdir(parents=True,exist_ok=True)
    results=[]
    with httpx.Client(timeout=httpx.Timeout(120,connect=15),follow_redirects=True,headers={'User-Agent':'NYCTrafficCourse/1.0','Accept-Encoding':'gzip'}) as client:
        for year in range(start.year,end.year+1):
            lo=max(start,date(year,1,1)); hi=min(end+timedelta(days=1),date(year+1,1,1))
            where=(f'yr = {year}' if kind=='traffic' else f"crash_date >= '{lo}T00:00:00' AND crash_date < '{hi}T00:00:00'")
            if kind=='traffic' and (lo!=date(year,1,1) or hi!=date(year+1,1,1)):
                raise ValueError('Traffic acquisition requires whole years; no partial-year silent expansion')
            manifest_path=ROOT/'data/manifests'/label/f'{kind}_{year}.json'
            endpoint=f'https://data.cityofnewyork.us/resource/{dataset}.json'
            meta=get(client,f'https://data.cityofnewyork.us/api/views/{dataset}.json').json()
            if manifest_path.exists():
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest['where']!=where or manifest['dataset']!=dataset: raise ValueError('Existing manifest has a different scope')
                for part in manifest['files']:
                    with gzip.open(ROOT/part['path'],'rb') as stream: raw=stream.read()
                    if hashlib.sha256(raw).hexdigest()!=part['sha256']: raise ValueError('Existing raw page failed integrity verification')
                if manifest['status']=='complete':
                    print(json.dumps({'kind':kind,'year':year,'cached':True,'rows':manifest['row_count']}),flush=True)
                    results.append(str(manifest_path.relative_to(ROOT))); continue
                if meta.get('rowsUpdatedAt')!=manifest['source_rows_updated']:
                    raise ValueError('Source changed during partial download; use a new label to preserve the interrupted evidence')
            else:
                count=int(get(client,endpoint,{'$select':'count(*) as n','$where':where}).json()[0]['n'])
                manifest={'schema_version':1,'kind':kind,'dataset':dataset,'label':label,'source_url':endpoint,'where':where,
                    'period_start':str(lo),'period_end':str(hi-timedelta(days=1)),'source_rows_updated':meta.get('rowsUpdatedAt'),
                    'captured_at':utcnow(),'expected_rows':count,'row_count':0,'status':'downloading','files':[],
                    'fields':[x['fieldName'] for x in meta['columns'] if not x.get('fieldName','').startswith(':')],
                    'license_url':'https://opendata.cityofnewyork.us/overview/#termsofuse'}
                save_json(folder/f'{year}_metadata.json',meta); save_json(manifest_path,manifest)
            last_key=manifest['files'][-1]['last_key'] if manifest['files'] else None
            while manifest['row_count']<manifest['expected_rows']:
                query_where=where
                if last_key is not None and key!=':id':
                    query_where+=f' AND {key} > {int(last_key)}'
                params={'$select':'*, :id as source_row_id','$where':query_where,'$order':key,'$limit':str(page_size)}
                if key==':id':params['$offset']=str(manifest['row_count'])
                began=time.monotonic(); response=get(client,endpoint,params); raw=response.content; rows=response.json()
                if not isinstance(rows,list) or not rows:raise ValueError('Source ended before expected row count')
                value_key='source_row_id' if key==':id' else key
                keys=[r.get(value_key) for r in rows]
                if any(k is None for k in keys):raise ValueError('Missing source key; cannot paginate safely')
                numeric=[str(k) if key==':id' else int(k) for k in keys]
                if (key!=':id' and numeric!=sorted(numeric)) or len(set(numeric))!=len(numeric):raise ValueError('Source keys are not ordered/unique')
                if last_key is not None and key!=':id' and numeric[0]<=int(last_key):raise ValueError('Page overlaps previous page')
                page=len(manifest['files'])+1; target=folder/f'{year}_page_{page:04d}.json.gz'
                if target.exists():
                    with gzip.open(target,'rb') as stream: previous_raw=stream.read()
                    if previous_raw!=raw: raise ValueError('Unregistered raw page differs from source; preserve and inspect')
                else:
                    temporary=target.with_suffix(target.suffix+'.part')
                    with gzip.open(temporary,'wb',compresslevel=5) as stream:stream.write(raw)
                    temporary.replace(target)
                digest=hashlib.sha256(raw).hexdigest()
                part={'path':str(target.relative_to(ROOT)).replace('\\','/'),'rows':len(rows),'sha256':digest,'bytes':len(raw),
                    'first_key':keys[0],'last_key':keys[-1],'url':str(response.url),'captured_at':utcnow(),'elapsed_seconds':round(time.monotonic()-began,2)}
                manifest['files'].append(part);manifest['row_count']+=len(rows);last_key=keys[-1]
                save_json(manifest_path,manifest)
                print(json.dumps({'kind':kind,'year':year,'rows':manifest['row_count'],'expected':manifest['expected_rows'],'seconds':part['elapsed_seconds']}),flush=True)
            seen=set()
            for part in manifest['files']:
                with gzip.open(ROOT/part['path'],'rt',encoding='utf-8') as stream:
                    for row in json.load(stream):
                        identity=row['source_row_id' if key==':id' else key]
                        if identity in seen:raise ValueError('Duplicate key across downloaded pages')
                        seen.add(identity)
            after_meta=get(client,f'https://data.cityofnewyork.us/api/views/{dataset}.json').json()
            after_count=int(get(client,endpoint,{'$select':'count(*) as n','$where':where}).json()[0]['n'])
            manifest['verified_at']=utcnow();manifest['count_after']=after_count
            if manifest['row_count']!=manifest['expected_rows'] or after_count!=manifest['expected_rows'] or after_meta.get('rowsUpdatedAt')!=manifest['source_rows_updated']:
                manifest['status']='source_changed';save_json(manifest_path,manifest)
                raise ValueError('Source state/count changed; snapshot has not been marked complete')
            manifest['checksum']=hashlib.sha256(''.join(x['sha256'] for x in manifest['files']).encode()).hexdigest()
            manifest['status']='complete';save_json(manifest_path,manifest)
            results.append(str(manifest_path.relative_to(ROOT)))
    return results
