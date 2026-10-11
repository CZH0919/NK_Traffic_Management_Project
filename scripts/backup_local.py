"""Local app-account custom-format backup; never print connection secrets."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.conf import settings

def main(label,tools_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',label): raise ValueError('Invalid backup label')
    folder=ROOT/'var/backups'; folder.mkdir(exist_ok=True)
    name=f'{label}_{datetime.now():%Y%m%d_%H%M%S}.dump'; target=folder/name
    if target.exists(): raise ValueError('Refusing to overwrite existing backup')
    db=settings.DATABASES['default']; env=os.environ.copy(); env['PGPASSWORD']=db['PASSWORD']
    pg_dump=Path(tools_dir)/'pg_dump.exe'; pg_restore=Path(tools_dir)/'pg_restore.exe'
    result=subprocess.run([str(pg_dump),'-h',db['HOST'],'-p',str(db['PORT']),'-U',db['USER'],
        '-d',db['NAME'],'-Fc','-f',str(target)],env=env,capture_output=True)
    if result.returncode: raise RuntimeError('Backup failed; inspect the local database service')
    verification_error=''
    try:
        result=subprocess.run([str(pg_restore),'--list',str(target)],capture_output=True)
        valid=result.returncode==0 and b'TABLE DATA' in result.stdout
        if not valid: verification_error='archive_list_failed'
    except OSError as error:
        valid=False
        verification_error=f'pg_restore_os_error_{getattr(error,"winerror",None) or error.errno}'
    with target.open('rb') as archive:
        header_readable=archive.read(5)==b'PGDMP'
    receipt={'captured_at':datetime.now().astimezone().isoformat(),'file':target.relative_to(ROOT).as_posix(),
        'bytes':target.stat().st_size,'pg_dump_exit_success':True,'custom_header_readable':header_readable,
        'archive_list_verified':valid,'verification_error':verification_error,'restore_exercise_performed':False}
    target.with_suffix('.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt),flush=True)
    if not valid: raise RuntimeError('Backup saved with receipt; archive directory verification is incomplete')

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--label',required=True)
    parser.add_argument('--tools-dir',required=True,help='Existing PostgreSQL bin directory')
    args=parser.parse_args(); main(args.label,args.tools_dir)
