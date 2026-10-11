"""Own local web/worker trees; stop through Ctrl+C or a token-bound request."""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
import psutil

ROOT=Path(__file__).resolve().parents[1]

def stop_children(children):
    # Windows venv launchers spawn a base-interpreter child. Collect only our
    # Popen descendants; never stop all Python processes or the database service.
    owned=[]
    for child in children:
        try:
            parent=psutil.Process(child.pid)
            owned.extend(reversed(parent.children(recursive=True))); owned.append(parent)
        except psutil.NoSuchProcess: pass
    for proc in owned:
        try: proc.terminate()
        except psutil.NoSuchProcess: pass
    _,alive=psutil.wait_procs(owned,timeout=5)
    for proc in alive:
        try: proc.kill()
        except psutil.NoSuchProcess: pass
    psutil.wait_procs(alive,timeout=3)
    for child in children:
        try: child.wait(timeout=1)
        except subprocess.TimeoutExpired: pass

def request_stop(state_path,stop_path):
    if not state_path.exists(): print('No managed local service is running.'); return 0
    data=json.loads(state_path.read_text(encoding='utf-8'))
    try:
        proc=psutil.Process(data['pid'])
        if proc.create_time()!=data['created_at']: raise psutil.NoSuchProcess(data['pid'])
    except psutil.NoSuchProcess:
        print('The recorded launcher has exited; inspect any independently started service.'); return 1
    stop_path.write_text(data['token'],encoding='ascii')
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if not state_path.exists(): print('Local web and worker stopped. PostgreSQL was not stopped.'); return 0
        time.sleep(.2)
    print('Stop request sent; the launcher has not yet confirmed shutdown.'); return 1

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8000); parser.add_argument('--stop',action='store_true'); parser.add_argument('--restore-run',help='Verified restore UUID; selects isolated database and files together.'); args=parser.parse_args()
    if not 1024<=args.port<=65535: parser.error('Port must be 1024..65535')
    folder=ROOT/'var/runtime'; folder.mkdir(parents=True,exist_ok=True)
    state=folder/f'local_{args.port}.json'; stop=folder/f'local_{args.port}.stop'
    if args.stop: return request_stop(state,stop)
    child_env=os.environ.copy()
    if args.restore_run:
        sys.path.insert(0,str(ROOT));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
        import django
        django.setup()
        from apps.core.maintenance import restored_environment
        from django.db import connections
        try:child_env.update(restored_environment(args.restore_run))
        except Exception:print('Verified isolated restore is unavailable; no service started.');return 1
        finally:connections.close_all()
    lock=(folder/f'local_{args.port}.lock').open('a+b')
    if lock.tell()==0: lock.write(b'0'); lock.flush()
    lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        lock.close(); print('A managed service on this port is already running.'); return 1
    children=[]; token=uuid.uuid4().hex
    try:
        stop.unlink(missing_ok=True)
        state.write_text(json.dumps({'pid':os.getpid(),'created_at':psutil.Process().create_time(),'token':token}),encoding='utf-8')
        children.append(subprocess.Popen([sys.executable,'manage.py','run_import_worker'],cwd=ROOT,env=child_env))
        children.append(subprocess.Popen([sys.executable,'manage.py','run_analysis_worker'],cwd=ROOT,env=child_env))
        children.append(subprocess.Popen([sys.executable,'manage.py','runserver',f'127.0.0.1:{args.port}','--insecure','--noreload'],cwd=ROOT,env=child_env))
        print(f'Local service: http://127.0.0.1:{args.port}/ ; stop: python scripts/run_local.py --stop --port {args.port}',flush=True)
        while all(child.poll() is None for child in children):
            if stop.exists() and stop.read_text(encoding='ascii').strip()==token: return 0
            time.sleep(.3)
        return next((child.returncode for child in children if child.returncode is not None and child.returncode!=0),0)
    except KeyboardInterrupt: return 0
    finally:
        stop_children(children); state.unlink(missing_ok=True); stop.unlink(missing_ok=True); lock.close()

if __name__=='__main__': raise SystemExit(main())
