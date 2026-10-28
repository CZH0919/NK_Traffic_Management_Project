"""Install the pinned offline reference map. Run explicitly, never at service startup.

Usage: python scripts/prepare_map_assets.py --work-dir PATH [--pmtiles-cli PATH]
The optional CLI path is needed only to extract a missing regional archive.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'config' / 'map_resources.json'


def fetch(entry, work):
    target = work / entry['filename']
    algorithm = entry.get('algorithm', 'sha256')
    expected = entry['digest']
    def valid():
        if not target.is_file():
            return False
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, algorithm).digest()
        actual = base64.b64encode(digest).decode() if algorithm == 'sha512' else digest.hex()
        return actual == expected
    if valid():
        return target
    temporary = target.with_suffix(target.suffix + '.download')
    request = urllib.request.Request(entry['url'], headers={'User-Agent': 'NKTrafficMapSetup/1.0'})
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > entry.get('max_bytes', 32 * 1024 * 1024):
                raise RuntimeError('Resource exceeds its download budget: ' + entry['filename'])
            output.write(chunk)
    # Validate before replacing an installed resource.
    with temporary.open('rb') as stream:
        digest = hashlib.file_digest(stream, algorithm).digest()
    actual = base64.b64encode(digest).decode() if algorithm == 'sha512' else digest.hex()
    if actual != expected:
        raise RuntimeError('Resource checksum mismatch: ' + entry['filename'])
    temporary.replace(target)
    return target


def install_assets(lock, work):
    for entry in lock['downloads']:
        package = fetch(entry, work)
        if entry.get('copies'):
            with tarfile.open(package) as archive:
                for member, relative in entry['copies'].items():
                    destination = ROOT / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.extractfile(member).read())
        elif entry.get('asset_tree'):
            with tarfile.open(package) as archive:
                for member in archive.getmembers():
                    relative = Path(*Path(member.name).parts[1:])
                    # Only known font stacks and v4 sprites; no archive extraction of paths or links.
                    if not member.isfile() or '..' in relative.parts:
                        continue
                    asset_name = str(relative).replace('\\', '/')
                    allowed = (asset_name == 'fonts/OFL.txt' or
                               any(asset_name.startswith('fonts/'+font+'/') for font in
                                   ('Noto Sans Regular','Noto Sans Medium','Noto Sans Italic')) or
                               asset_name.startswith('sprites/v4/'))
                    if not allowed:
                        continue
                    destination = ROOT / 'static/vendor/basemap-assets' / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.extractfile(member).read())
        elif entry.get('destination'):
            destination = ROOT / entry['destination']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(package.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--pmtiles-cli', type=Path)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    install_assets(lock, args.work_dir)
    spec = lock['basemap']
    target = ROOT / spec['path']
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        if not args.pmtiles_cli:
            raise SystemExit('Reference assets installed. Supply --pmtiles-cli to extract the offline archive.')
        cli = args.pmtiles_cli.resolve(strict=True)
        temporary = target.with_suffix('.pmtiles.part')
        if temporary.exists():
            raise SystemExit('A previous partial archive exists; inspect it before retrying.')
        env = {**os.environ, 'GOMAXPROCS': '2'}
        subprocess.run([str(cli), 'extract', spec['url'], str(temporary),
                        '--bbox=' + ','.join(map(str, spec['bounds'])),
                        '--maxzoom=15', '--download-threads=2'], env=env, check=True)
        subprocess.run([str(cli), 'verify', str(temporary)], env=env, check=True)
        with temporary.open('rb') as stream:
            valid = hashlib.file_digest(stream, 'sha256').hexdigest() == spec['sha256']
        if not valid or temporary.stat().st_size > 500 * 1024 * 1024:
            raise SystemExit('Archive verification failed; existing files were not replaced.')
        temporary.replace(target)
    with target.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != spec['sha256']:
            raise SystemExit('Installed archive does not match the pinned regional snapshot.')
    print('Offline reference map and local assets verified:', target.name, target.stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
