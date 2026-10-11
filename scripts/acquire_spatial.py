"""Acquire pinned LION 26b and borough boundaries; retain native evidence."""
import argparse
import gzip
import hashlib
import json
import sys
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import pyogrio
from apps.ingestion.acquisition import ROOT, get, save_json, utcnow

LION_URL = 'https://data.cityofnewyork.us/api/views/2v4z-66xt/files/30298730-5064-447c-8c39-a3981409d9b3?download=true&filename=nyclion.zip'

def page(folder, name, rows):
    raw = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    path = folder / name
    digest = hashlib.sha256(raw).hexdigest()
    if path.exists():
        with gzip.open(path, 'rb') as f:
            if hashlib.sha256(f.read()).hexdigest() != digest:
                raise ValueError('Existing spatial evidence differs; use a new label')
    else:
        with gzip.open(path, 'xb', compresslevel=5) as f: f.write(raw)
    return {'path':path.relative_to(ROOT).as_posix(), 'rows':len(rows), 'sha256':digest, 'bytes':len(raw)}

def main(label):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', label): raise ValueError('Invalid label')
    folder = ROOT / 'data/raw' / label / 'spatial'
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        archive = folder / 'nyclion_26b.zip'
        if not archive.exists():
            response = get(client, LION_URL)
            with archive.open('xb') as f: f.write(response.content)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        extracted = folder / 'lion26b'
        with zipfile.ZipFile(archive) as z:
            if z.testzip(): raise ValueError('Corrupt archive')
            for member in z.infolist():
                target = (extracted / member.filename).resolve()
                if not target.is_relative_to(extracted.resolve()): raise ValueError('Unsafe archive member')
                if not target.exists(): z.extract(member, extracted)
        gdb = extracted / 'lion/lion.gdb'
        for kind, layer in [('nodes','node'), ('roads','lion')]:
            target = ROOT / 'data/manifests' / label / f'{kind}_26b.json'
            if target.exists():
                saved = json.loads(target.read_text(encoding='utf-8'))
                if saved.get('archive_checksum') != digest: raise ValueError('Archive changed')
            info = pyogrio.read_info(gdb, layer=layer)
            if info['crs'] != 'EPSG:2263': raise ValueError('Unexpected native CRS')
            parts = []
            for offset in range(0, info['features'], 20000):
                frame = pyogrio.read_dataframe(gdb, layer=layer, skip_features=offset, max_features=20000, fid_as_index=True)
                attrs = frame.drop(columns='geometry').copy()
                attrs['_fid'] = frame.index
                attrs['_geometry_wkt'] = frame.geometry.to_wkt(rounding_precision=12)
                rows = json.loads(attrs.to_json(orient='records', force_ascii=False))
                parts.append(page(folder, f'{kind}_26b_{offset//20000+1:04}.json.gz', rows))
            manifest = dict(schema_version=1, kind=kind, dataset='2v4z-66xt', label=label,
                version='26b', source_url=LION_URL, captured_at=utcnow(), status='complete',
                row_count=info['features'], expected_rows=info['features'], native_crs=info['crs'],
                files=parts, archive_path=archive.relative_to(ROOT).as_posix(), archive_checksum=digest,
                fields=list(info['fields']), layer=layer,
                checksum=hashlib.sha256(''.join(p['sha256'] for p in parts).encode()).hexdigest())
            if not target.exists(): save_json(target, manifest)
            print(json.dumps({'kind':kind,'rows':manifest['row_count'],'crs':manifest['native_crs']}), flush=True)
        for dataset, scope in [('wh2p-dxnf','water_included'),('gthc-hcne','water_excluded')]:
            meta_path = folder / f'boroughs_{scope}_metadata.json'
            source_url = f'https://data.cityofnewyork.us/resource/{dataset}.json'
            if not meta_path.exists(): save_json(meta_path, get(client,f'https://data.cityofnewyork.us/api/views/{dataset}.json').json())
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
            if '26b' not in json.dumps(metadata).lower(): raise ValueError('Boundary version requires review')
            raw_path = folder / f'boroughs_{scope}.json.gz'
            if raw_path.exists():
                with gzip.open(raw_path,'rt',encoding='utf-8') as f: rows=json.load(f)
            else: rows=get(client,source_url,{'$limit':'10','$order':'borocode'}).json()
            if len(rows)!=5 or {int(r['borocode']) for r in rows}!={1,2,3,4,5}: raise ValueError('Incomplete boroughs')
            part=page(folder,raw_path.name,rows)
            if scope=='water_included':
                manifest=dict(schema_version=1,kind='boundaries',dataset=dataset,label=label,version='26b',
                    source_url=source_url,captured_at=utcnow(),status='complete',row_count=5,expected_rows=5,
                    native_crs='EPSG:4326',files=[part],water_areas='included',
                    display_boundary_file=(folder/'boroughs_water_excluded.json.gz').relative_to(ROOT).as_posix(),
                    checksum=hashlib.sha256(part['sha256'].encode()).hexdigest())
                target=ROOT/'data/manifests'/label/'boundaries_26b.json'
                if not target.exists(): save_json(target,manifest)
            print(json.dumps({'kind':'boundaries','scope':scope,'rows':len(rows)}),flush=True)
        references=folder.parent/'references'; references.mkdir(exist_ok=True)
        attachments={
            'traffic_dictionary.xlsx':'https://data.cityofnewyork.us/api/views/7ym2-wayt/files/634d4f24-db90-4444-905b-5fdfc975a607?download=true',
            'traffic_location_errors.xlsx':'https://data.cityofnewyork.us/api/views/7ym2-wayt/files/5d84797d-5e2e-45a7-8ad7-8d055d37c82a?download=true'}
        for name,url in attachments.items():
            target=references/name
            if not target.exists():
                with target.open('xb') as f: f.write(get(client,url).content)
            receipt=references/(name+'.receipt.json')
            if not receipt.exists(): save_json(receipt,{'source_url':url,'captured_at':utcnow(),
                'path':target.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--label',required=True)
    main(parser.parse_args().label)
