"""Acquire full-field source snapshots; does not connect to PostgreSQL."""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from apps.ingestion.acquisition import acquire_socrata

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    p.add_argument('--label',required=True)
    p.add_argument('--kinds',nargs='+',choices=['crashes','vehicles','persons','traffic'],default=['crashes','vehicles','persons','traffic'])
    p.add_argument('--workers',type=int,choices=[1,2,3],default=2)
    args=p.parse_args()
    if args.end<args.start:p.error('end precedes start')
    errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks={pool.submit(acquire_socrata,k,args.start,args.end,args.label):k for k in args.kinds}
        for task in as_completed(tasks):
            try: print(json.dumps({'completed':tasks[task],'manifests':task.result()}),flush=True)
            except Exception as exc:
                errors.append(tasks[task]);print(json.dumps({'failed':tasks[task],'error':str(exc),'type':type(exc).__name__}),flush=True)
    raise SystemExit(1 if errors else 0)

if __name__=='__main__':main()
