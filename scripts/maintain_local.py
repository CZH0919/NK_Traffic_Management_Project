"""Local administrator maintenance; no arbitrary SQL or existing-DB overwrite."""
import argparse,os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.contrib.auth.models import User
from apps.core import maintenance
from apps.core.models import MaintenanceRun


def main():
    parser=argparse.ArgumentParser(description='Audited local backup and isolated restoration.')
    parser.add_argument('operation',choices=['backup','restore'])
    parser.add_argument('--actor',required=True,help='Existing application administrator; local config access is also required.')
    parser.add_argument('--tools-dir',required=True)
    parser.add_argument('--backup-id');parser.add_argument('--target');parser.add_argument('--admin-password-file')
    args=parser.parse_args()
    if args.operation=='restore' and not all((args.backup_id,args.target,args.admin_password_file)):parser.error('restore requires --backup-id, --target and --admin-password-file')
    try:
        actor=User.objects.get(username=args.actor,is_active=True)
        if args.operation=='backup':run=maintenance.backup(actor,args.tools_dir)
        else:run=maintenance.restore(actor,MaintenanceRun.objects.get(pk=args.backup_id),args.target,args.tools_dir,args.admin_password_file)
        print(json.dumps({'id':str(run.pk),'operation':run.operation,'status':run.status,'target':run.target_label,'artifact':run.artifact_relative_path,'summary':run.summary},ensure_ascii=True))
        return 0
    except maintenance.MaintenanceError as e:print('Maintenance stopped: '+e.code);return 1
    except Exception as e:
        # Do not print DSNs, server exception details, credential paths or keys.
        print('Maintenance stopped: permission' if isinstance(e,maintenance.PermissionDenied) else 'Maintenance stopped: invalid_configuration')
        return 1


if __name__=='__main__':raise SystemExit(main())
